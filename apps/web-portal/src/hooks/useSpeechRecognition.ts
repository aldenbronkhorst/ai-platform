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

function getSpeechRecognitionConstructor() {
  if (typeof window === "undefined") return null;
  const speechWindow = window as SpeechRecognitionWindow;
  return speechWindow.SpeechRecognition || speechWindow.webkitSpeechRecognition || null;
}

function isPermissionDeniedError(err: unknown) {
  return typeof err === "object" && err !== null && "name" in err
    && ((err as { name?: string }).name === "NotAllowedError" || (err as { name?: string }).name === "SecurityError");
}

export function useSpeechRecognition(onTranscript: (transcript: string) => void) {
  const [voiceState, setVoiceState] = useState<VoiceState>(
    () => getSpeechRecognitionConstructor() ? "idle" : "unsupported",
  );
  const [interimTranscript, setInterimTranscript] = useState("");
  const recognitionRef = useRef<SpeechRecognitionLike | null>(null);
  const micStreamRef = useRef<MediaStream | null>(null);
  const shouldListenRef = useRef(false);
  const startInProgressRef = useRef(false);
  const restartTimerRef = useRef<number | null>(null);
  const stopFlushTimerRef = useRef<number | null>(null);
  const interimTranscriptRef = useRef("");
  const committedResultIndexesRef = useRef<Set<number>>(new Set());
  const onTranscriptRef = useRef(onTranscript);

  useEffect(() => {
    onTranscriptRef.current = onTranscript;
  }, [onTranscript]);

  const clearRestartTimer = useCallback(() => {
    if (restartTimerRef.current) {
      window.clearTimeout(restartTimerRef.current);
      restartTimerRef.current = null;
    }
  }, []);

  const clearStopFlushTimer = useCallback(() => {
    if (stopFlushTimerRef.current) {
      window.clearTimeout(stopFlushTimerRef.current);
      stopFlushTimerRef.current = null;
    }
  }, []);

  const releaseMicStream = useCallback(() => {
    micStreamRef.current?.getTracks().forEach(track => track.stop());
    micStreamRef.current = null;
  }, []);

  const ensureMicStream = useCallback(async () => {
    const existingStream = micStreamRef.current;
    if (existingStream?.getTracks().some(track => track.readyState === "live")) return;
    if (!window.navigator.mediaDevices?.getUserMedia) return;
    micStreamRef.current = await window.navigator.mediaDevices.getUserMedia({ audio: true });
  }, []);

  const clearInterimTranscript = useCallback(() => {
    interimTranscriptRef.current = "";
    setInterimTranscript("");
  }, []);

  const resetTranscriptBuffer = useCallback(() => {
    committedResultIndexesRef.current.clear();
    clearInterimTranscript();
  }, [clearInterimTranscript]);

  const emitTranscript = useCallback((transcript: string) => {
    const cleanTranscript = transcript.replace(/\s+/g, " ").trim();
    if (cleanTranscript) onTranscriptRef.current(cleanTranscript);
  }, []);

  const flushTranscriptBuffer = useCallback(() => {
    emitTranscript(interimTranscriptRef.current);
    clearInterimTranscript();
  }, [clearInterimTranscript, emitTranscript]);

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
      committedResultIndexesRef.current.clear();
      setVoiceState("listening");
    };
    recognition.onresult = (event) => {
      const finalSegments: string[] = [];
      const interimSegments: string[] = [];
      const startIndex = Math.max(0, event.resultIndex || 0);
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
      emitTranscript(finalSegments.join(" "));
      const interim = interimSegments.join(" ").replace(/\s+/g, " ").trim();
      interimTranscriptRef.current = interim;
      setInterimTranscript(interim);
    };
    recognition.onerror = (event) => {
      if (event.error === "not-allowed" || event.error === "service-not-allowed") {
        shouldListenRef.current = false;
        startInProgressRef.current = false;
        clearRestartTimer();
        clearStopFlushTimer();
        resetTranscriptBuffer();
        releaseMicStream();
        setVoiceState("denied");
        return;
      }
      if (event.error === "aborted") {
        flushTranscriptBuffer();
        startInProgressRef.current = false;
        if (!shouldListenRef.current) {
          releaseMicStream();
          setVoiceState("idle");
        }
        return;
      }
      setVoiceState(shouldListenRef.current ? "listening" : "idle");
    };
    recognition.onend = () => {
      startInProgressRef.current = false;
      clearStopFlushTimer();
      flushTranscriptBuffer();
      if (!shouldListenRef.current) {
        releaseMicStream();
        setVoiceState(prev => prev === "listening" || prev === "processing" ? "idle" : prev);
        return;
      }
      restartTimerRef.current = window.setTimeout(() => {
        restartTimerRef.current = null;
        try {
          committedResultIndexesRef.current.clear();
          recognition.start();
        } catch {
          shouldListenRef.current = false;
          releaseMicStream();
          setVoiceState("idle");
        }
      }, 300);
    };
    recognitionRef.current = recognition;

    return () => {
      shouldListenRef.current = false;
      clearRestartTimer();
      clearStopFlushTimer();
      resetTranscriptBuffer();
      releaseMicStream();
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
  }, [clearRestartTimer, clearStopFlushTimer, emitTranscript, flushTranscriptBuffer, releaseMicStream, resetTranscriptBuffer]);

  const toggleVoice = useCallback(() => {
    if (voiceState === "unsupported") return;
    if (shouldListenRef.current || startInProgressRef.current || voiceState === "listening" || voiceState === "processing") {
      shouldListenRef.current = false;
      startInProgressRef.current = false;
      clearRestartTimer();
      clearStopFlushTimer();
      try {
        recognitionRef.current?.stop();
      } catch {
        // Ignore stop races when the browser recognition engine already ended.
      }
      stopFlushTimerRef.current = window.setTimeout(() => {
        stopFlushTimerRef.current = null;
        flushTranscriptBuffer();
        releaseMicStream();
        setVoiceState("idle");
      }, 800);
      setVoiceState("processing");
      return;
    }

    void (async () => {
      startInProgressRef.current = true;
      shouldListenRef.current = true;
      resetTranscriptBuffer();
      setVoiceState("processing");
      try {
        await ensureMicStream();
        if (!shouldListenRef.current) {
          startInProgressRef.current = false;
          releaseMicStream();
          setVoiceState("idle");
          return;
        }
        recognitionRef.current?.start();
        setVoiceState("listening");
      } catch (err) {
        shouldListenRef.current = false;
        startInProgressRef.current = false;
        clearRestartTimer();
        clearStopFlushTimer();
        resetTranscriptBuffer();
        releaseMicStream();
        setVoiceState(isPermissionDeniedError(err) ? "denied" : "idle");
      }
    })();
  }, [clearRestartTimer, clearStopFlushTimer, ensureMicStream, flushTranscriptBuffer, releaseMicStream, resetTranscriptBuffer, voiceState]);

  return { voiceState, toggleVoice, interimTranscript };
}
