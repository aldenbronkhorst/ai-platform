import { useCallback, useEffect, useRef, useState } from "react";
import type { VoiceState } from "../types";

interface SpeechRecognitionResultLike {
  resultIndex?: number;
  results: {
    length: number;
    [index: number]: {
      isFinal?: boolean;
      [index: number]: {
        transcript: string;
      };
    };
  };
}

interface SpeechRecognitionErrorLike {
  error: string;
}

interface SpeechRecognitionLike {
  continuous: boolean;
  interimResults: boolean;
  lang: string;
  onstart: (() => void) | null;
  onresult: ((event: SpeechRecognitionResultLike) => void) | null;
  onerror: ((event: SpeechRecognitionErrorLike) => void) | null;
  onend: (() => void) | null;
  start: () => void;
  stop: () => void;
  abort: () => void;
}

type SpeechRecognitionConstructor = new () => SpeechRecognitionLike;

type SpeechRecognitionWindow = Window & {
  SpeechRecognition?: SpeechRecognitionConstructor;
  webkitSpeechRecognition?: SpeechRecognitionConstructor;
};

type AudioContextWindow = Window & {
  webkitAudioContext?: typeof AudioContext;
};

interface SpeechRecognitionOptions {
  transcribeAudio?: (audio: Blob) => Promise<string>;
}

const TARGET_SAMPLE_RATE = 16000;

function getSpeechRecognitionConstructor() {
  if (typeof window === "undefined") return null;
  const speechWindow = window as SpeechRecognitionWindow;
  return speechWindow.SpeechRecognition || speechWindow.webkitSpeechRecognition || null;
}

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
  options: SpeechRecognitionOptions = {},
) {
  const [voiceState, setVoiceState] = useState<VoiceState>(
    () => (getSpeechRecognitionConstructor() || canRecordAudio()) ? "idle" : "unsupported",
  );
  const [interimTranscript, setInterimTranscript] = useState("");
  const recognitionRef = useRef<SpeechRecognitionLike | null>(null);
  const mediaStreamRef = useRef<MediaStream | null>(null);
  const audioContextRef = useRef<AudioContext | null>(null);
  const audioSourceRef = useRef<MediaStreamAudioSourceNode | null>(null);
  const audioProcessorRef = useRef<ScriptProcessorNode | null>(null);
  const audioChunksRef = useRef<Float32Array[]>([]);
  const recordingSampleRateRef = useRef(TARGET_SAMPLE_RATE);
  const shouldListenRef = useRef(false);
  const startInProgressRef = useRef(false);
  const audioTranscriptionInProgressRef = useRef(false);
  const stopFlushTimerRef = useRef<number | null>(null);
  const restartTimerRef = useRef<number | null>(null);
  const interimTranscriptRef = useRef("");
  const spokenTranscriptRef = useRef("");
  const emittedTranscriptRef = useRef("");
  const finalTranscriptEmittedRef = useRef(false);
  const committedResultIndexesRef = useRef<Set<number>>(new Set());
  const onTranscriptRef = useRef(onTranscript);
  const transcribeAudioRef = useRef(options.transcribeAudio);

  useEffect(() => {
    onTranscriptRef.current = onTranscript;
  }, [onTranscript]);

  useEffect(() => {
    transcribeAudioRef.current = options.transcribeAudio;
  }, [options.transcribeAudio]);

  const clearStopFlushTimer = useCallback(() => {
    if (stopFlushTimerRef.current) {
      window.clearTimeout(stopFlushTimerRef.current);
      stopFlushTimerRef.current = null;
    }
  }, []);

  const clearRestartTimer = useCallback(() => {
    if (restartTimerRef.current) {
      window.clearTimeout(restartTimerRef.current);
      restartTimerRef.current = null;
    }
  }, []);

  const normalizeTranscript = useCallback((transcript: string) => {
    return transcript.replace(/\s+/g, " ").trim();
  }, []);

  const clearInterimTranscript = useCallback(() => {
    interimTranscriptRef.current = "";
    setInterimTranscript("");
  }, []);

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

  const resetTranscriptBuffer = useCallback(() => {
    spokenTranscriptRef.current = "";
    emittedTranscriptRef.current = "";
    finalTranscriptEmittedRef.current = false;
    committedResultIndexesRef.current.clear();
    clearInterimTranscript();
  }, [clearInterimTranscript]);

  const emitTranscript = useCallback((transcript: string) => {
    const cleanTranscript = normalizeTranscript(transcript);
    if (cleanTranscript) onTranscriptRef.current(cleanTranscript);
  }, [normalizeTranscript]);

  const pendingTranscript = useCallback(() => {
    const spoken = normalizeTranscript(spokenTranscriptRef.current);
    const emitted = normalizeTranscript(emittedTranscriptRef.current);
    if (!spoken) return normalizeTranscript(interimTranscriptRef.current);
    if (!emitted) return spoken;
    if (spoken.toLowerCase().startsWith(emitted.toLowerCase())) {
      return normalizeTranscript(spoken.slice(emitted.length));
    }
    const interim = normalizeTranscript(interimTranscriptRef.current);
    return interim && !emitted.toLowerCase().includes(interim.toLowerCase()) ? interim : "";
  }, [normalizeTranscript]);

  const markTranscriptEmitted = useCallback((transcript: string) => {
    const cleanTranscript = normalizeTranscript(transcript);
    if (!cleanTranscript) return;
    emittedTranscriptRef.current = normalizeTranscript(
      `${emittedTranscriptRef.current} ${cleanTranscript}`,
    );
  }, [normalizeTranscript]);

  const hasFinalTranscriptEmitted = useCallback(() => finalTranscriptEmittedRef.current, []);

  const flushTranscriptBuffer = useCallback(() => {
    const pending = pendingTranscript();
    emitTranscript(pending);
    markTranscriptEmitted(pending);
    clearInterimTranscript();
  }, [clearInterimTranscript, emitTranscript, markTranscriptEmitted, pendingTranscript]);

  const handleRecordingStopped = useCallback(async () => {
    const chunks = audioChunksRef.current;
    audioChunksRef.current = [];
    cleanupRecording();

    const transcribeAudio = transcribeAudioRef.current;
    if (hasFinalTranscriptEmitted()) {
      clearInterimTranscript();
      if (!shouldListenRef.current) {
        setVoiceState(prev => prev === "denied" || prev === "unsupported" ? prev : "idle");
      }
      return;
    }

    if (!chunks.length || !transcribeAudio) {
      flushTranscriptBuffer();
      if (!shouldListenRef.current) {
        setVoiceState(prev => prev === "denied" || prev === "unsupported" ? prev : "idle");
      }
      return;
    }

    audioTranscriptionInProgressRef.current = true;
    setVoiceState("processing");
    try {
      const mergedSamples = mergeAudioChunks(chunks);
      const wavSamples = downsampleAudio(mergedSamples, recordingSampleRateRef.current, TARGET_SAMPLE_RATE);
      const blob = encodeWav(wavSamples, TARGET_SAMPLE_RATE);
      const transcript = await transcribeAudio(blob);
      emitTranscript(transcript);
      markTranscriptEmitted(transcript);
      finalTranscriptEmittedRef.current = true;
      clearInterimTranscript();
      setVoiceState("idle");
    } catch (err) {
      console.error("Voice transcription failed:", err);
      clearInterimTranscript();
      setVoiceState("idle");
    } finally {
      audioTranscriptionInProgressRef.current = false;
    }
  }, [
    cleanupRecording,
    clearInterimTranscript,
    emitTranscript,
    flushTranscriptBuffer,
    hasFinalTranscriptEmitted,
    markTranscriptEmitted,
  ]);

  const startAudioRecording = useCallback(async () => {
    if (!canRecordAudio() || !transcribeAudioRef.current) return false;
    try {
      const AudioContextConstructor = getAudioContextConstructor();
      if (!AudioContextConstructor) return false;
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      if (!shouldListenRef.current) {
        stream.getTracks().forEach(track => track.stop());
        return false;
      }

      const audioContext = new AudioContextConstructor();
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
      return true;
    } catch (err) {
      cleanupRecording();
      shouldListenRef.current = false;
      startInProgressRef.current = false;
      resetTranscriptBuffer();
      setVoiceState(isPermissionDeniedError(err) ? "denied" : "idle");
      return false;
    }
  }, [cleanupRecording, resetTranscriptBuffer]);

  const startRecognition = useCallback(() => {
    if (!shouldListenRef.current || startInProgressRef.current) return false;
    const recognition = recognitionRef.current;
    if (!recognition) return false;
    clearRestartTimer();
    startInProgressRef.current = true;
    setVoiceState("processing");
    try {
      recognition.start();
      setVoiceState("listening");
      return true;
    } catch (err) {
      startInProgressRef.current = false;
      shouldListenRef.current = false;
      clearRestartTimer();
      resetTranscriptBuffer();
      setVoiceState(isPermissionDeniedError(err) ? "denied" : "idle");
      return false;
    }
  }, [clearRestartTimer, resetTranscriptBuffer]);

  useEffect(() => {
    const SpeechRecognition = getSpeechRecognitionConstructor();
    if (!SpeechRecognition) return;

    const recognition = new SpeechRecognition();
    recognition.continuous = true;
    recognition.interimResults = true;
    recognition.lang = window.navigator.language || "en-US";
    recognition.onstart = () => {
      startInProgressRef.current = false;
      clearStopFlushTimer();
      clearRestartTimer();
      committedResultIndexesRef.current.clear();
      setVoiceState("listening");
    };
    recognition.onresult = (event) => {
      const finalSegments: string[] = [];
      const interimSegments: string[] = [];
      const allSegments: string[] = [];
      const startIndex = Math.max(0, event.resultIndex || 0);
      for (let i = 0; i < event.results.length; i += 1) {
        const transcript = event.results[i][0]?.transcript?.trim();
        if (transcript) allSegments.push(transcript);
      }
      spokenTranscriptRef.current = normalizeTranscript(allSegments.join(" "));
      for (let i = startIndex; i < event.results.length; i += 1) {
        const result = event.results[i];
        const transcript = result[0]?.transcript?.trim();
        if (!transcript) continue;
        if (result.isFinal !== false && !committedResultIndexesRef.current.has(i)) {
          committedResultIndexesRef.current.add(i);
          finalSegments.push(transcript);
        } else if (result.isFinal === false) {
          interimSegments.push(transcript);
        }
      }
      const finalTranscript = finalSegments.join(" ");
      emitTranscript(finalTranscript);
      markTranscriptEmitted(finalTranscript);
      if (normalizeTranscript(finalTranscript)) {
        finalTranscriptEmittedRef.current = true;
      }
      const pending = pendingTranscript();
      const interim = pending || normalizeTranscript(interimSegments.join(" "));
      interimTranscriptRef.current = interim;
      setInterimTranscript(interim);
    };
    recognition.onerror = (event) => {
      if (event.error === "not-allowed" || event.error === "service-not-allowed") {
        shouldListenRef.current = false;
        startInProgressRef.current = false;
        clearStopFlushTimer();
        clearRestartTimer();
        resetTranscriptBuffer();
        cleanupRecording();
        setVoiceState("denied");
        return;
      }
      if (event.error === "aborted") {
        if (!audioContextRef.current) flushTranscriptBuffer();
        startInProgressRef.current = false;
        clearRestartTimer();
        if (!shouldListenRef.current && !audioContextRef.current) setVoiceState("idle");
        return;
      }
      if (event.error === "no-speech") {
        if (!audioContextRef.current) flushTranscriptBuffer();
        startInProgressRef.current = false;
        if (shouldListenRef.current) setVoiceState("listening");
        return;
      }
      flushTranscriptBuffer();
      shouldListenRef.current = false;
      startInProgressRef.current = false;
      clearRestartTimer();
      cleanupRecording();
      setVoiceState("idle");
    };
    recognition.onend = () => {
      startInProgressRef.current = false;
      clearStopFlushTimer();
      if (shouldListenRef.current) {
        setVoiceState("listening");
        restartTimerRef.current = window.setTimeout(() => {
          restartTimerRef.current = null;
          startRecognition();
        }, 250);
        return;
      }
      if (audioTranscriptionInProgressRef.current || audioContextRef.current) return;
      flushTranscriptBuffer();
      setVoiceState(prev => prev === "listening" || prev === "processing" ? "idle" : prev);
    };
    recognitionRef.current = recognition;

    return () => {
      shouldListenRef.current = false;
      clearStopFlushTimer();
      clearRestartTimer();
      resetTranscriptBuffer();
      cleanupRecording();
      recognition.onstart = null;
      recognition.onresult = null;
      recognition.onerror = null;
      recognition.onend = null;
      try {
        recognition.abort();
      } catch {
        // Ignore abort races during unmount.
      }
      recognitionRef.current = null;
    };
  }, [
    cleanupRecording,
    clearStopFlushTimer,
    clearRestartTimer,
    emitTranscript,
    flushTranscriptBuffer,
    markTranscriptEmitted,
    normalizeTranscript,
    pendingTranscript,
    resetTranscriptBuffer,
    startRecognition,
  ]);

  const stopVoice = useCallback(() => {
    shouldListenRef.current = false;
    startInProgressRef.current = false;
    clearStopFlushTimer();
    clearRestartTimer();
    try {
      recognitionRef.current?.stop();
    } catch {
      // Ignore stop races when the browser recognition engine already ended.
    }

    if (audioContextRef.current) {
      setVoiceState("processing");
      void handleRecordingStopped();
      return;
    }

    flushTranscriptBuffer();

    stopFlushTimerRef.current = window.setTimeout(() => {
      stopFlushTimerRef.current = null;
      flushTranscriptBuffer();
      cleanupRecording();
      setVoiceState("idle");
    }, 500);
    setVoiceState("processing");
  }, [cleanupRecording, clearRestartTimer, clearStopFlushTimer, flushTranscriptBuffer, handleRecordingStopped]);

  const toggleVoice = useCallback(() => {
    if (voiceState === "unsupported") return;
    if (shouldListenRef.current || startInProgressRef.current || voiceState === "listening" || voiceState === "processing") {
      stopVoice();
      return;
    }

    shouldListenRef.current = true;
    resetTranscriptBuffer();
    setVoiceState("processing");
    void (async () => {
      const recordingStarted = await startAudioRecording();
      if (!shouldListenRef.current) return;
      const recognitionStarted = startRecognition();
      if (recordingStarted || recognitionStarted) {
        setVoiceState("listening");
        return;
      }
      shouldListenRef.current = false;
      setVoiceState((getSpeechRecognitionConstructor() || canRecordAudio()) ? "idle" : "unsupported");
    })();
  }, [resetTranscriptBuffer, startAudioRecording, startRecognition, stopVoice, voiceState]);

  return { voiceState, toggleVoice, interimTranscript };
}
