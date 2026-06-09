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
        resetTranscriptBuffer();
        setVoiceState("denied");
        return;
      }
      if (event.error === "aborted") {
        flushTranscriptBuffer();
        startInProgressRef.current = false;
        shouldListenRef.current = false;
        setVoiceState("idle");
        return;
      }
      flushTranscriptBuffer();
      shouldListenRef.current = false;
      startInProgressRef.current = false;
      setVoiceState("idle");
    };
    recognition.onend = () => {
      startInProgressRef.current = false;
      clearStopFlushTimer();
      flushTranscriptBuffer();
      shouldListenRef.current = false;
      setVoiceState(prev => prev === "listening" || prev === "processing" ? "idle" : prev);
    };
    recognitionRef.current = recognition;

    return () => {
      shouldListenRef.current = false;
      clearStopFlushTimer();
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
    emitTranscript,
    flushTranscriptBuffer,
    markTranscriptEmitted,
    normalizeTranscript,
    pendingTranscript,
    resetTranscriptBuffer,
  ]);

  const toggleVoice = useCallback(() => {
    if (voiceState === "unsupported") return;
    if (shouldListenRef.current || startInProgressRef.current || voiceState === "listening" || voiceState === "processing") {
      shouldListenRef.current = false;
      startInProgressRef.current = false;
      clearStopFlushTimer();
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

    void (async () => {
      startInProgressRef.current = true;
      shouldListenRef.current = true;
      resetTranscriptBuffer();
      setVoiceState("processing");
      try {
        if (!shouldListenRef.current) {
          startInProgressRef.current = false;
          setVoiceState("idle");
          return;
        }
        recognitionRef.current?.start();
        setVoiceState("listening");
      } catch (err) {
        shouldListenRef.current = false;
        startInProgressRef.current = false;
        clearStopFlushTimer();
        resetTranscriptBuffer();
        setVoiceState(isPermissionDeniedError(err) ? "denied" : "idle");
      }
    })();
  }, [clearStopFlushTimer, flushTranscriptBuffer, resetTranscriptBuffer, voiceState]);

  return { voiceState, toggleVoice, interimTranscript };
}
