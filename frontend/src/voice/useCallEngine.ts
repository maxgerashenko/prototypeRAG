import { useCallback, useRef, useState } from "react";
import type { BusinessOut } from "../api/types";
import { CallEngine, type CallSnapshot } from "./CallEngine";

export interface StartCallCallbacks {
  onConnected: () => void;
  onCancelled: () => void;
  onConnectFailed: (message: string) => void;
  onUnknownBusiness: () => void;
  onEnded: (snapshot: CallSnapshot) => void;
}

/** React wrapper around CallEngine: keeps the engine instance in a ref (so it's never
 * recreated by a re-render) and mirrors its snapshots into state for rendering. */
export function useCallEngine() {
  const engineRef = useRef<CallEngine | null>(null);
  const [snapshot, setSnapshot] = useState<CallSnapshot | null>(null);

  const startCall = useCallback(
    (bizId: string, biz: BusinessOut | null, continueFrom: string | null, cb: StartCallCallbacks) => {
      const engine = new CallEngine(bizId, biz, continueFrom, {
        onUpdate: setSnapshot,
        onConnected: cb.onConnected,
        onCancelled: cb.onCancelled,
        onConnectFailed: cb.onConnectFailed,
        onUnknownBusiness: cb.onUnknownBusiness,
        onEnded: cb.onEnded,
      });
      engineRef.current = engine;
      setSnapshot(engine.snapshot());
      void engine.start();
    },
    [],
  );

  const engine = engineRef.current;
  return {
    snapshot,
    startCall,
    pttDown: () => engine?.pttDown(),
    pttUp: () => engine?.pttUp(),
    end: () => engine?.end(),
    cancelConnecting: () => engine?.cancelConnecting(),
  };
}
