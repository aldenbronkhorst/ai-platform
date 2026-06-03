import { useCallback, useEffect, useRef, useState } from "react";
import type { VoiceState } from "../types";

interface SpeechRecognitionResultLike {
  results: {
    [index: number]: {
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
  const onTranscriptRef = useRef(onTranscript);

  useEffect(() => {
    onTranscriptRef.current = onTranscript;
  }, [onTranscript]);

  useEffect(() => {
    const SpeechRecognition = getSpeechRecognitionConstructor();
    if (!SpeechRecognition) return;

    const recognition = new SpeechRecognition();
    recognition.continuous = false;
    recognition.interimResults = false;
    recognition.lang = "en-US";
    recognition.onstart = () => setVoiceState("listening");
    recognition.onresult = (event) => {
      onTranscriptRef.current(event.results[0][0].transcript);
      setVoiceState("processing");
    };
    recognition.onerror = (event) => {
      setVoiceState(event.error === "not-allowed" ? "denied" : "idle");
    };
    recognition.onend = () => {
      setVoiceState(prev => prev === "listening" || prev === "processing" ? "idle" : prev);
    };
    recognitionRef.current = recognition;

    return () => {
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
      recognitionRef.current?.stop();
      return;
    }
    try {
      recognitionRef.current?.start();
    } catch {
      // Ignore duplicate start requests.
    }
  }, [voiceState]);

  return { voiceState, toggleVoice };
}
