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
  const onTranscriptRef = useRef(onTranscript);

  useEffect(() => {
    onTranscriptRef.current = onTranscript;
  }, [onTranscript]);

  useEffect(() => {
    const SpeechRecognition = getSpeechRecognitionConstructor();
    if (!SpeechRecognition) return;

    const recognition = new SpeechRecognition();
    recognition.continuous = true;
    recognition.interimResults = false;
    recognition.lang = "en-US";
    recognition.onstart = () => setVoiceState("listening");
    recognition.onresult = (event) => {
      const finalSegments: string[] = [];
      const startIndex = event.resultIndex ?? 0;
      for (let i = startIndex; i < event.results.length; i += 1) {
        const result = event.results[i];
        if (result.isFinal === false) continue;
        const transcript = result[0]?.transcript?.trim();
        if (transcript) finalSegments.push(transcript);
      }
      if (finalSegments.length > 0) {
        onTranscriptRef.current(finalSegments.join(" "));
      }
    };
    recognition.onerror = (event) => {
      if (event.error === "not-allowed" || event.error === "service-not-allowed") {
        shouldListenRef.current = false;
        if (restartTimerRef.current) {
          window.clearTimeout(restartTimerRef.current);
          restartTimerRef.current = null;
        }
        setVoiceState("denied");
        return;
      }
      setVoiceState(shouldListenRef.current ? "listening" : "idle");
    };
    recognition.onend = () => {
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
      if (restartTimerRef.current) {
        window.clearTimeout(restartTimerRef.current);
        restartTimerRef.current = null;
      }
      try {
        recognition.abort();
      } catch {
        // Ignore abort races during unmount.
      }
      recognitionRef.current = null;
    };
  }, []);

  const toggleVoice = useCallback(() => {
    if (voiceState === "unsupported") return;
    if (voiceState === "listening") {
      shouldListenRef.current = false;
      if (restartTimerRef.current) {
        window.clearTimeout(restartTimerRef.current);
        restartTimerRef.current = null;
      }
      recognitionRef.current?.stop();
      return;
    }
    try {
      shouldListenRef.current = true;
      recognitionRef.current?.start();
      setVoiceState("listening");
    } catch {
      shouldListenRef.current = false;
      // Ignore duplicate start requests.
    }
  }, [voiceState]);

  return { voiceState, toggleVoice };
}
