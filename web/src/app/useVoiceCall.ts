import { useCallback, useEffect, useReducer, useRef } from "react";
import { VoiceCall } from "../voice/voiceCall";
import { NO_SPEECH_MS, callReducer, initialCallState, type CallState } from "./callState";

export interface VoiceCallApi {
  state: CallState;
  /** Starts a call; resolves once mic + socket are up, rejects if they fail. */
  start: (businessId: string) => Promise<void>;
  hangUp: () => void;
  pttDown: () => void;
  pttUp: () => void;
}

/** One live call at a time, hold-to-talk (DEC-38). */
export function useVoiceCall(): VoiceCallApi {
  const [state, dispatch] = useReducer(callReducer, initialCallState);
  const call = useRef<VoiceCall | null>(null);
  const noSpeechTimer = useRef<number | undefined>(undefined);
  const talking = useRef(false);

  const clearNoSpeech = () => window.clearTimeout(noSpeechTimer.current);

  const start = useCallback(async (businessId: string) => {
    call.current?.stop();
    clearNoSpeech();
    talking.current = false;
    dispatch({ type: "reset" });
    const c: VoiceCall = new VoiceCall(
      {
        onEvent: (ev) => {
          if (call.current !== c) return;
          if (ev.type === "transcript") clearNoSpeech();
          dispatch({ type: "event", ev, now: Date.now() });
        },
        onPlayback: (active) => {
          if (call.current === c) dispatch({ type: "playback", active });
        },
        onClose: (code, reason, byServer) => {
          if (call.current === c) dispatch({ type: "closed", code, reason, byServer, now: Date.now() });
        },
        onEnded: () => {
          if (call.current !== c) return;
          call.current = null;
          clearNoSpeech();
          dispatch({ type: "hangup", now: Date.now() });
        },
      },
      { transmitting: false },
    );
    call.current = c;
    try {
      await c.start(businessId);
    } catch (err) {
      c.stop(); // releases the mic if it was granted before the failure
      throw err;
    }
  }, []);

  const hangUp = useCallback(() => {
    const c = call.current;
    if (!c) return;
    dispatch({ type: "hangup", now: Date.now() });
    c.stop();
  }, []);

  const pttDown = useCallback(() => {
    if (!call.current || talking.current) return;
    talking.current = true;
    clearNoSpeech();
    call.current.setTalking(true);
    dispatch({ type: "pttDown" });
  }, []);

  const pttUp = useCallback(() => {
    if (!talking.current) return;
    talking.current = false;
    call.current?.setTalking(false);
    dispatch({ type: "pttUp" });
    clearNoSpeech();
    noSpeechTimer.current = window.setTimeout(() => dispatch({ type: "noSpeech" }), NO_SPEECH_MS);
  }, []);

  // leaving the page mid-call hangs up
  useEffect(
    () => () => {
      clearNoSpeech();
      call.current?.stop();
    },
    [],
  );

  return { state, start, hangUp, pttDown, pttUp };
}
