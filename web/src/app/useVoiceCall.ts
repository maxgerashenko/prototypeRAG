import { useCallback, useEffect, useReducer, useRef, useState } from "react";
import { isMobileBrowser } from "./mobile";
import { VoiceCall } from "../voice/voiceCall";
import { NO_SPEECH_MS, callReducer, initialCallState, type CallState } from "./callState";

export interface VoiceCallApi {
  state: CallState;
  /** Starts a call (optionally continuing an earlier conversation); resolves once mic +
   * socket are up, rejects if they fail. */
  start: (businessId: string, continueFrom?: string) => Promise<void>;
  hangUp: () => void;
  pttDown: () => void;
  pttUp: () => void;
  /** True on a mobile browser: no push-to-talk, the server's VAD takes turns. */
  handsFree: boolean;
}

/** One live call at a time, server push-to-talk (DEC-42); hands-free on mobile browsers (#114). */
export function useVoiceCall(): VoiceCallApi {
  const [state, dispatch] = useReducer(callReducer, initialCallState);
  const call = useRef<VoiceCall | null>(null);
  const noSpeechTimer = useRef<number | undefined>(undefined);
  const talking = useRef(false);
  const live = useRef(false); // push-to-talk only once the server confirmed the call
  const [handsFree] = useState(isMobileBrowser);
  useEffect(() => {
    live.current = state.phase === "live";
  }, [state.phase]);

  const clearNoSpeech = () => window.clearTimeout(noSpeechTimer.current);

  const start = useCallback(async (businessId: string, continueFrom?: string) => {
    call.current?.stop();
    clearNoSpeech();
    talking.current = false;
    dispatch({ type: "reset" });
    const c: VoiceCall = new VoiceCall(
      {
        onEvent: (ev) => {
          if (call.current !== c) return;
          if (ev.type === "transcript" || ev.type === "no_speech") clearNoSpeech();
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
      { mode: handsFree ? "vad" : "ptt", continueFrom },
    );
    call.current = c;
    try {
      await c.start(businessId);
    } catch (err) {
      c.stop(); // releases the mic if it was granted before the failure
      throw err;
    }
  }, [handsFree]);

  const hangUp = useCallback(() => {
    const c = call.current;
    if (!c) return;
    dispatch({ type: "hangup", now: Date.now() });
    c.stop();
  }, []);

  const pttDown = useCallback(() => {
    if (handsFree) return;
    if (!call.current || !live.current || talking.current) return;
    talking.current = true;
    clearNoSpeech();
    call.current.setTalking(true);
    dispatch({ type: "pttDown" });
  }, [handsFree]);

  const pttUp = useCallback(() => {
    if (handsFree) return;
    if (!talking.current) return;
    talking.current = false;
    call.current?.setTalking(false);
    dispatch({ type: "pttUp" });
    clearNoSpeech();
    noSpeechTimer.current = window.setTimeout(() => dispatch({ type: "noSpeech" }), NO_SPEECH_MS);
  }, [handsFree]);

  // leaving the page mid-call hangs up
  useEffect(
    () => () => {
      clearNoSpeech();
      call.current?.stop();
    },
    [],
  );

  return { state, handsFree, start, hangUp, pttDown, pttUp };
}
