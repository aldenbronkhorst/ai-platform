import { useCallback, useEffect, useRef, useState } from "react";
import type { VoiceState } from "../types";

type AudioContextWindow = Window & {
  webkitAudioContext?: typeof AudioContext;
};

interface VoiceInputOptions {
  transcribeAudio?: (audio: Blob) => Promise<string>;
}

const TARGET_SAMPLE_RATE = 16000;
const MIN_AUDIO_SECONDS = 0.35;
const SILENCE_THRESHOLD = 0.012;
const TRIM_PADDING_SECONDS = 0.15;
const NORMALIZE_BELOW_PEAK = 0.25;
const NORMALIZE_TARGET_PEAK = 0.85;

function getAudioContextConstructor() {
  if (typeof window === "undefined") return null;
  const audioWindow = window as AudioContextWindow;
  return window.AudioContext || audioWindow.webkitAudioContext || null;
}

function canRecordAudio() {
  return typeof window !== "undefined"
    && typeof navigator !== "undefined"
    && Boolean(navigator.mediaDevices?.getUserMedia)
    && Boolean(getAudioContextConstructor());
}

function mergeAudioChunks(chunks: Float32Array[]) {
  const totalLength = chunks.reduce((sum, chunk) => sum + chunk.length, 0);
  const merged = new Float32Array(totalLength);
  let offset = 0;
  chunks.forEach((chunk) => {
    merged.set(chunk, offset);
    offset += chunk.length;
  });
  return merged;
}

function downsampleAudio(input: Float32Array, inputSampleRate: number, outputSampleRate: number) {
  if (inputSampleRate === outputSampleRate) return input;
  const ratio = inputSampleRate / outputSampleRate;
  const outputLength = Math.max(1, Math.round(input.length / ratio));
  const output = new Float32Array(outputLength);
  let inputOffset = 0;
  for (let outputOffset = 0; outputOffset < outputLength; outputOffset += 1) {
    const nextInputOffset = Math.round((outputOffset + 1) * ratio);
    let sum = 0;
    let count = 0;
    for (let i = inputOffset; i < nextInputOffset && i < input.length; i += 1) {
      sum += input[i];
      count += 1;
    }
    output[outputOffset] = count ? sum / count : 0;
    inputOffset = nextInputOffset;
  }
  return output;
}

function trimAndNormalizeAudio(input: Float32Array, sampleRate: number) {
  if (!input.length) return input;

  let firstVoiceSample = -1;
  let lastVoiceSample = -1;
  let peak = 0;

  for (let i = 0; i < input.length; i += 1) {
    const abs = Math.abs(input[i]);
    if (abs > peak) peak = abs;
    if (abs >= SILENCE_THRESHOLD) {
      if (firstVoiceSample === -1) firstVoiceSample = i;
      lastVoiceSample = i;
    }
  }

  const padding = Math.round(TRIM_PADDING_SECONDS * sampleRate);
  const start = firstVoiceSample === -1 ? 0 : Math.max(0, firstVoiceSample - padding);
  const end = lastVoiceSample === -1 ? input.length : Math.min(input.length, lastVoiceSample + padding);
  const trimmed = input.slice(start, Math.max(start + 1, end));

  if (peak <= 0 || peak >= NORMALIZE_BELOW_PEAK) return trimmed;

  const gain = Math.min(NORMALIZE_TARGET_PEAK / peak, 12);
  const normalized = new Float32Array(trimmed.length);
  for (let i = 0; i < trimmed.length; i += 1) {
    normalized[i] = Math.max(-1, Math.min(1, trimmed[i] * gain));
  }
  return normalized;
}

function writeAscii(view: DataView, offset: number, value: string) {
  for (let i = 0; i < value.length; i += 1) {
    view.setUint8(offset + i, value.charCodeAt(i));
  }
}

function encodeWav(samples: Float32Array, sampleRate: number) {
  const dataLength = samples.length * 2;
  const buffer = new ArrayBuffer(44 + dataLength);
  const view = new DataView(buffer);
  writeAscii(view, 0, "RIFF");
  view.setUint32(4, 36 + dataLength, true);
  writeAscii(view, 8, "WAVE");
  writeAscii(view, 12, "fmt ");
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);
  view.setUint16(22, 1, true);
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * 2, true);
  view.setUint16(32, 2, true);
  view.setUint16(34, 16, true);
  writeAscii(view, 36, "data");
  view.setUint32(40, dataLength, true);
  let offset = 44;
  for (let i = 0; i < samples.length; i += 1) {
    const sample = Math.max(-1, Math.min(1, samples[i]));
    view.setInt16(offset, sample < 0 ? sample * 0x8000 : sample * 0x7fff, true);
    offset += 2;
  }
  return new Blob([view], { type: "audio/wav" });
}

function isPermissionDeniedError(err: unknown) {
  return typeof err === "object" && err !== null && "name" in err
    && ((err as { name?: string }).name === "NotAllowedError" || (err as { name?: string }).name === "SecurityError");
}

export function useSpeechRecognition(
  onTranscript: (transcript: string) => void,
  options: VoiceInputOptions = {},
) {
  const [voiceState, setVoiceState] = useState<VoiceState>(() => canRecordAudio() ? "idle" : "unsupported");
  const mediaStreamRef = useRef<MediaStream | null>(null);
  const audioContextRef = useRef<AudioContext | null>(null);
  const audioSourceRef = useRef<MediaStreamAudioSourceNode | null>(null);
  const audioProcessorRef = useRef<ScriptProcessorNode | null>(null);
  const audioChunksRef = useRef<Float32Array[]>([]);
  const recordingSampleRateRef = useRef(TARGET_SAMPLE_RATE);
  const shouldListenRef = useRef(false);
  const startInProgressRef = useRef(false);
  const onTranscriptRef = useRef(onTranscript);
  const transcribeAudioRef = useRef(options.transcribeAudio);

  useEffect(() => {
    onTranscriptRef.current = onTranscript;
  }, [onTranscript]);

  useEffect(() => {
    transcribeAudioRef.current = options.transcribeAudio;
  }, [options.transcribeAudio]);

  const stopMediaTracks = useCallback(() => {
    mediaStreamRef.current?.getTracks().forEach(track => track.stop());
    mediaStreamRef.current = null;
  }, []);

  const cleanupRecording = useCallback(() => {
    audioProcessorRef.current?.disconnect();
    audioSourceRef.current?.disconnect();
    audioProcessorRef.current = null;
    audioSourceRef.current = null;
    const audioContext = audioContextRef.current;
    audioContextRef.current = null;
    if (audioContext && audioContext.state !== "closed") {
      void audioContext.close().catch(() => undefined);
    }
    stopMediaTracks();
  }, [stopMediaTracks]);

  useEffect(() => {
    return () => {
      shouldListenRef.current = false;
      cleanupRecording();
    };
  }, [cleanupRecording]);

  const startRecording = useCallback(async () => {
    const transcribeAudio = transcribeAudioRef.current;
    const AudioContextConstructor = getAudioContextConstructor();
    if (!transcribeAudio || !AudioContextConstructor || !canRecordAudio()) {
      setVoiceState("unsupported");
      return;
    }

    startInProgressRef.current = true;
    shouldListenRef.current = true;
    setVoiceState("processing");

    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
        },
      });
      if (!shouldListenRef.current) {
        stream.getTracks().forEach(track => track.stop());
        return;
      }

      const audioContext = new AudioContextConstructor();
      if (audioContext.state === "suspended") {
        await audioContext.resume();
      }
      const source = audioContext.createMediaStreamSource(stream);
      const processor = audioContext.createScriptProcessor(4096, 1, 1);

      mediaStreamRef.current = stream;
      audioContextRef.current = audioContext;
      audioSourceRef.current = source;
      audioProcessorRef.current = processor;
      audioChunksRef.current = [];
      recordingSampleRateRef.current = audioContext.sampleRate;

      processor.onaudioprocess = (event) => {
        if (!shouldListenRef.current) return;
        const input = event.inputBuffer.getChannelData(0);
        audioChunksRef.current.push(new Float32Array(input));
        event.outputBuffer.getChannelData(0).fill(0);
      };

      source.connect(processor);
      processor.connect(audioContext.destination);
      setVoiceState("listening");
    } catch (err) {
      shouldListenRef.current = false;
      cleanupRecording();
      setVoiceState(isPermissionDeniedError(err) ? "denied" : "idle");
    } finally {
      startInProgressRef.current = false;
    }
  }, [cleanupRecording]);

  const stopRecording = useCallback(() => {
    shouldListenRef.current = false;
    startInProgressRef.current = false;
    setVoiceState("processing");

    const chunks = audioChunksRef.current;
    audioChunksRef.current = [];
    const inputSampleRate = recordingSampleRateRef.current;
    cleanupRecording();

    void (async () => {
      try {
        const transcribeAudio = transcribeAudioRef.current;
        if (!chunks.length || !transcribeAudio) {
          setVoiceState(canRecordAudio() ? "idle" : "unsupported");
          return;
        }

        const mergedSamples = mergeAudioChunks(chunks);
        const wavSamples = downsampleAudio(mergedSamples, inputSampleRate, TARGET_SAMPLE_RATE);
        const processedSamples = trimAndNormalizeAudio(wavSamples, TARGET_SAMPLE_RATE);
        if (processedSamples.length / TARGET_SAMPLE_RATE < MIN_AUDIO_SECONDS) {
          setVoiceState("idle");
          return;
        }

        const transcript = (await transcribeAudio(encodeWav(processedSamples, TARGET_SAMPLE_RATE))).trim();
        if (transcript) onTranscriptRef.current(transcript);
      } catch (err) {
        console.error("Voice transcription failed:", err);
      } finally {
        setVoiceState(canRecordAudio() ? "idle" : "unsupported");
      }
    })();
  }, [cleanupRecording]);

  const toggleVoice = useCallback(() => {
    if (voiceState === "unsupported") return;
    if (shouldListenRef.current || startInProgressRef.current || voiceState === "listening" || voiceState === "processing") {
      stopRecording();
      return;
    }
    void startRecording();
  }, [startRecording, stopRecording, voiceState]);

  return { voiceState, toggleVoice, interimTranscript: "" };
}
