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

export function useSpeechRecognition(onTranscript: (transcript: string) => void) {
  const [voiceState, setVoiceState] = useState<VoiceState>(
    () => getSpeechRecognitionConstructor() ? "idle" : "unsupported",
  );
  const recognitionRef = useRef<SpeechRecognitionLike | null>(null);
  const shouldListenRef = useRef(false);
  const restartTimerRef = useRef<number | null>(null);
  const stopFlushTimerRef = useRef<number | null>(null);
  const finalTranscriptRef = useRef("");
  const interimTranscriptRef = useRef("");
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

  const resetTranscriptBuffer = useCallback(() => {
    finalTranscriptRef.current = "";
    interimTranscriptRef.current = "";
  }, []);

  const flushTranscriptBuffer = useCallback(() => {
    const transcript = [
      finalTranscriptRef.current,
      interimTranscriptRef.current,
    ].filter(Boolean).join(" ").replace(/\s+/g, " ").trim();

    resetTranscriptBuffer();
    if (transcript) {
      onTranscriptRef.current(transcript);
    }
  }, [resetTranscriptBuffer]);

  useEffect(() => {
    const SpeechRecognition = getSpeechRecognitionConstructor();
    if (!SpeechRecognition) return;

    const recognition = new SpeechRecognition();
    recognition.continuous = true;
    recognition.interimResults = true;
    recognition.lang = window.navigator.language || "en-US";
    recognition.onstart = () => {
      clearStopFlushTimer();
      setVoiceState("listening");
    };
    recognition.onresult = (event) => {
      const finalSegments: string[] = [];
      const interimSegments: string[] = [];
      for (let i = 0; i < event.results.length; i += 1) {
        const result = event.results[i];
        const transcript = result[0]?.transcript?.trim();
        if (!transcript) continue;
        if (result.isFinal === false) {
          interimSegments.push(transcript);
        } else {
          finalSegments.push(transcript);
        }
      }
      finalTranscriptRef.current = finalSegments.join(" ");
      interimTranscriptRef.current = interimSegments.join(" ");
    };
    recognition.onerror = (event) => {
      if (event.error === "not-allowed" || event.error === "service-not-allowed") {
        shouldListenRef.current = false;
        clearRestartTimer();
        clearStopFlushTimer();
        resetTranscriptBuffer();
        setVoiceState("denied");
        return;
      }
      if (event.error === "aborted") {
        flushTranscriptBuffer();
        setVoiceState("idle");
        return;
      }
      setVoiceState(shouldListenRef.current ? "listening" : "idle");
    };
    recognition.onend = () => {
      clearStopFlushTimer();
      flushTranscriptBuffer();
      if (!shouldListenRef.current) {
        setVoiceState(prev => prev === "listening" || prev === "processing" ? "idle" : prev);
        return;
      }
      restartTimerRef.current = window.setTimeout(() => {
        restartTimerRef.current = null;
        try {
          recognition.start();
        } catch {
          shouldListenRef.current = false;
          setVoiceState("idle");
        }
      }, 150);
    };
    recognitionRef.current = recognition;

    return () => {
      shouldListenRef.current = false;
      clearRestartTimer();
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
  }, [clearRestartTimer, clearStopFlushTimer, flushTranscriptBuffer, resetTranscriptBuffer]);

  const toggleVoice = useCallback(() => {
    if (voiceState === "unsupported") return;
    if (shouldListenRef.current || voiceState === "listening" || voiceState === "processing") {
      shouldListenRef.current = false;
      clearRestartTimer();
      clearStopFlushTimer();
      recognitionRef.current?.stop();
      stopFlushTimerRef.current = window.setTimeout(() => {
        stopFlushTimerRef.current = null;
        flushTranscriptBuffer();
        setVoiceState("idle");
      }, 800);
      setVoiceState("processing");
      return;
    }
    try {
      resetTranscriptBuffer();
      shouldListenRef.current = true;
      recognitionRef.current?.start();
      setVoiceState("listening");
    } catch {
      shouldListenRef.current = false;
      // Ignore duplicate start requests.
    }
  }, [clearRestartTimer, clearStopFlushTimer, flushTranscriptBuffer, resetTranscriptBuffer, voiceState]);

  return { voiceState, toggleVoice };
}
