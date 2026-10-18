import { useEffect, useReducer } from "react";
import type { LiveFeedMessage } from "../api";
import { liveCallsReducer, type PhoneCalls } from "./liveCalls";

const reducer = (calls: PhoneCalls, msg: LiveFeedMessage) => liveCallsReducer(calls, msg, Date.now());

/** Phone calls in progress, live from GET /voice/feed (DEC-44). The feed is open only while
 * the page is visible: an open stream keeps a Cloud Run instance busy (DEC-35), and the
 * snapshot sent on reconnect catches the page up when it's shown again. */
export function useLiveCalls(): PhoneCalls {
  const [calls, dispatch] = useReducer(reducer, {});

  useEffect(() => {
    let source: EventSource | null = null;
    const open = () => {
      if (source || document.hidden) return;
      source = new EventSource("/voice/feed");
      // EventSource reconnects by itself after an error; a snapshot follows each reconnect
      source.onmessage = (m: MessageEvent<string>) => {
        try {
          dispatch(JSON.parse(m.data) as LiveFeedMessage);
        } catch {
          // not JSON: ignore
        }
      };
    };
    const close = () => {
      source?.close();
      source = null;
    };
    const visibility = () => (document.hidden ? close() : open());
    open();
    document.addEventListener("visibilitychange", visibility);
    return () => {
      document.removeEventListener("visibilitychange", visibility);
      close();
    };
  }, []);

  return calls;
}
