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
  const shouldListenRef = useRef(false);
  const startInProgressRef = useRef(false);
  const stopFlushTimerRef = useRef<number | null>(null);
  const restartTimerRef = useRef<number | null>(null);
  const interimTranscriptRef = useRef("");
  const spokenTranscriptRef = useRef("");
  const emittedTranscriptRef = useRef("");
  const committedResultIndexesRef = useRef<Set<number>>(new Set());
  const onTranscriptRef = useRef(onTranscript);

  useEffect(() => {
    onTranscriptRef.current = onTranscript;
  }, [onTranscript]);

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

  const resetTranscriptBuffer = useCallback(() => {
    spokenTranscriptRef.current = "";
    emittedTranscriptRef.current = "";
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

  const flushTranscriptBuffer = useCallback(() => {
    const pending = pendingTranscript();
    emitTranscript(pending);
    markTranscriptEmitted(pending);
    clearInterimTranscript();
  }, [clearInterimTranscript, emitTranscript, markTranscriptEmitted, pendingTranscript]);

  const startRecognition = useCallback(() => {
    if (!shouldListenRef.current || startInProgressRef.current) return;
    const recognition = recognitionRef.current;
    if (!recognition) {
      shouldListenRef.current = false;
      setVoiceState("unsupported");
      return;
    }
    clearRestartTimer();
    startInProgressRef.current = true;
    setVoiceState("processing");
    try {
      recognition.start();
      setVoiceState("listening");
    } catch (err) {
      startInProgressRef.current = false;
      shouldListenRef.current = false;
      clearRestartTimer();
      resetTranscriptBuffer();
      setVoiceState(isPermissionDeniedError(err) ? "denied" : "idle");
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
        setVoiceState("denied");
        return;
      }
      if (event.error === "aborted") {
        flushTranscriptBuffer();
        startInProgressRef.current = false;
        clearRestartTimer();
        if (!shouldListenRef.current) setVoiceState("idle");
        return;
      }
      if (event.error === "no-speech") {
        flushTranscriptBuffer();
        startInProgressRef.current = false;
        if (shouldListenRef.current) setVoiceState("listening");
        return;
      }
      flushTranscriptBuffer();
      shouldListenRef.current = false;
      startInProgressRef.current = false;
      clearRestartTimer();
      setVoiceState("idle");
    };
    recognition.onend = () => {
      startInProgressRef.current = false;
      clearStopFlushTimer();
      flushTranscriptBuffer();
      if (shouldListenRef.current) {
        setVoiceState("listening");
        restartTimerRef.current = window.setTimeout(() => {
          restartTimerRef.current = null;
          startRecognition();
        }, 250);
        return;
      }
      setVoiceState(prev => prev === "listening" || prev === "processing" ? "idle" : prev);
    };
    recognitionRef.current = recognition;

    return () => {
      shouldListenRef.current = false;
      clearStopFlushTimer();
      clearRestartTimer();
      resetTranscriptBuffer();
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

  const toggleVoice = useCallback(() => {
    if (voiceState === "unsupported") return;
    if (shouldListenRef.current || startInProgressRef.current || voiceState === "listening" || voiceState === "processing") {
      shouldListenRef.current = false;
      startInProgressRef.current = false;
      clearStopFlushTimer();
      clearRestartTimer();
      try {
        recognitionRef.current?.stop();
      } catch {
        // Ignore stop races when the browser recognition engine already ended.
      }
      stopFlushTimerRef.current = window.setTimeout(() => {
        stopFlushTimerRef.current = null;
        flushTranscriptBuffer();
        setVoiceState("idle");
      }, 800);
      setVoiceState("processing");
      return;
    }

    shouldListenRef.current = true;
    resetTranscriptBuffer();
    startRecognition();
  }, [clearRestartTimer, clearStopFlushTimer, flushTranscriptBuffer, resetTranscriptBuffer, startRecognition, voiceState]);

  return { voiceState, toggleVoice, interimTranscript };
}
