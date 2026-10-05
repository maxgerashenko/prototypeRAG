// Voice app (DEC-42): the "Voice Chat Bot" design, working against the real backend —
// businesses (app/api/businesses.py) and saved conversations (app/api/conversations.py),
// live calls over /voice/browser with server push-to-talk. Six screens, one at a time, like
// the design's stages. Keyboard and error handling follow the earlier plain-HTML version of
// this app (main's web/mic-test.html): Space calls / holds to talk, Esc backs out or ends
// the call, Backspace ends a call, "Unknown business" returns to the list.

import { useCallback, useEffect, useRef, useState } from "react";
import {
  getConversation,
  listBusinesses,
  listConversations,
  type Business,
  type ConversationDetail,
  type ConversationSummary,
} from "../api";
import { CallScreen, ConnectingScreen, EndedScreen } from "./CallScreens";
import { ConversationsScreen } from "./ConversationsScreen";
import { SelectScreen } from "./SelectScreen";
import { TranscriptScreen } from "./TranscriptScreen";
import { useVoiceCall } from "./useVoiceCall";
import { callerSpoke } from "./callState";

export interface Loadable<T> {
  status: "loading" | "ok" | "error";
  data?: T;
  error?: string;
}

type Stage = "select" | "convos" | "transcript" | "connecting" | "call" | "ended";

interface TranscriptRef {
  bizId: string;
  convoId: string;
  returnTo: "select" | "convos";
}

const errorText = (err: unknown) => (err instanceof Error ? err.message : String(err));

declare global {
  interface Window {
    /** Read by web/public/dev-reload.js (DEV_RELOAD=true): never reload during a call. */
    devReloadBlocked?: () => boolean;
  }
}

/** Banner text for a call the server closed before or during the call. */
function closeMessage(code: number | null, reason: string | null): string {
  if (code === 4404) return "Unknown business";
  return reason ? `Connection failed: ${reason}` : "Connection failed";
}

export function App() {
  const [stage, setStage] = useState<Stage>("select");
  const [businesses, setBusinesses] = useState<Loadable<Business[]>>({ status: "loading" });
  const [convos, setConvos] = useState<Record<string, Loadable<ConversationSummary[]>>>({});
  const [query, setQuery] = useState("");
  const [selId, setSelId] = useState<string | null>(null);
  const [newIds, setNewIds] = useState<ReadonlySet<string>>(new Set());
  const [banner, setBanner] = useState<string | null>(null);
  const [transcript, setTranscript] = useState<TranscriptRef | null>(null);
  const [detail, setDetail] = useState<Loadable<ConversationDetail>>({ status: "loading" });
  const [callBizId, setCallBizId] = useState<string | null>(null);
  const voice = useVoiceCall();
  const call = voice.state;
  const detailReq = useRef(0);

  const loadBusinesses = useCallback(() => {
    setBusinesses((prev) => ({ ...prev, status: "loading" }));
    listBusinesses().then(
      (data) => setBusinesses({ status: "ok", data }),
      (err) => setBusinesses((prev) => ({ ...prev, status: "error", error: errorText(err) })),
    );
  }, []);

  const loadConvos = useCallback((bizId: string) => {
    setConvos((prev) => ({ ...prev, [bizId]: { ...prev[bizId], status: "loading" } }));
    listConversations(bizId).then(
      (data) => setConvos((prev) => ({ ...prev, [bizId]: { status: "ok", data } })),
      (err) => setConvos((prev) => ({ ...prev, [bizId]: { ...prev[bizId], status: "error", error: errorText(err) } })),
    );
  }, []);

  useEffect(loadBusinesses, [loadBusinesses]);

  const openConversation = (bizId: string, convoId: string, returnTo: TranscriptRef["returnTo"]) => {
    const req = ++detailReq.current;
    setTranscript({ bizId, convoId, returnTo });
    setDetail({ status: "loading" });
    setStage("transcript");
    getConversation(bizId, convoId).then(
      (data) => req === detailReq.current && setDetail({ status: "ok", data }),
      (err) => req === detailReq.current && setDetail({ status: "error", error: errorText(err) }),
    );
  };

  const startCall = (bizId: string, continueFrom?: string) => {
    setBanner(null);
    setCallBizId(bizId);
    setSelId(bizId);
    setStage("connecting");
    voice.start(bizId, continueFrom).catch((err: unknown) => {
      setBanner(`Couldn’t start the call: ${errorText(err)}`);
      setStage("select");
    });
  };

  const cancelCall = () => {
    setStage("select");
    voice.hangUp();
  };

  const backToConversations = () => {
    if (callBizId) {
      setSelId(callBizId);
      setStage("convos");
    } else setStage("select");
  };

  // follow the call: live -> call screen; closed while connecting -> back with the reason;
  // ended -> summary, and the new conversation shows up (marked NEW) in the lists
  useEffect(() => {
    if (stage === "connecting" && call.phase === "live") setStage("call");
    if (stage === "connecting" && call.phase === "ended") {
      if (call.endReason) setBanner(closeMessage(call.endCode, call.endReason));
      setStage("select");
    }
    if (stage === "call" && call.phase === "ended") {
      loadBusinesses();
      if (callBizId) loadConvos(callBizId);
      if (call.endCode === 4404) {
        setBanner(closeMessage(call.endCode, call.endReason));
        setStage("select");
        return;
      }
      setStage("ended");
      if (call.conversationId && callerSpoke(call)) setNewIds((prev) => new Set(prev).add(call.conversationId!));
    }
  }, [stage, call, callBizId, loadBusinesses, loadConvos]);

  // keyboard (as in the plain-HTML app); ignored while typing in a text field
  const onKey = useRef<(e: KeyboardEvent, down: boolean) => void>(() => {});
  onKey.current = (e, down) => {
    const el = document.activeElement;
    if (el && (el.tagName === "INPUT" || el.tagName === "TEXTAREA")) return;
    if (e.key === " ") {
      e.preventDefault();
      if (!down) {
        if (stage === "call") voice.pttUp();
        return;
      }
      if (e.repeat) return;
      if (stage === "select" && selId) startCall(selId);
      else if (stage === "convos" && selId) startCall(selId);
      else if (stage === "transcript" && transcript) startCall(transcript.bizId, transcript.convoId);
      else if (stage === "call") voice.pttDown();
    } else if (down && e.key === "Escape") {
      if (stage === "connecting") cancelCall();
      else if (stage === "call") voice.hangUp();
      else if (stage === "convos") setStage("select");
      else if (stage === "transcript" && transcript) setStage(transcript.returnTo);
      else if (stage === "ended") backToConversations();
    } else if (down && e.key === "Backspace") {
      if (stage === "connecting") cancelCall();
      else if (stage === "call") voice.hangUp();
    }
  };
  const stageRef = useRef(stage);
  stageRef.current = stage;
  useEffect(() => {
    const keydown = (e: KeyboardEvent) => onKey.current(e, true);
    const keyup = (e: KeyboardEvent) => onKey.current(e, false);
    const release = () => voice.pttUp(); // no stuck talk button after alt-tab / hidden tab
    const hidden = () => document.hidden && voice.pttUp();
    window.addEventListener("keydown", keydown);
    window.addEventListener("keyup", keyup);
    window.addEventListener("blur", release);
    document.addEventListener("visibilitychange", hidden);
    window.devReloadBlocked = () => stageRef.current === "connecting" || stageRef.current === "call";
    return () => {
      window.removeEventListener("keydown", keydown);
      window.removeEventListener("keyup", keyup);
      window.removeEventListener("blur", release);
      document.removeEventListener("visibilitychange", hidden);
    };
  }, [voice.pttUp]);

  const all = businesses.data ?? [];
  const find = (id: string | null) => all.find((b) => b.id === id);
  const fallbackBiz = (id: string | null): Business =>
    find(id) ?? { id: id ?? "", name: "Business", website: null, domain: null, conversation_count: 0, default_location: null };

  return (
    <div className="vapp">
      {stage === "select" && (
        <SelectScreen
          businesses={businesses}
          convos={convos}
          newIds={newIds}
          query={query}
          selId={selId}
          banner={banner}
          onQuery={setQuery}
          onPick={(id) => {
            setSelId(id);
            if (id) loadConvos(id);
          }}
          onOpen={(bizId, convoId) => openConversation(bizId, convoId, "select")}
          onSeeAll={(bizId) => {
            setSelId(bizId);
            setStage("convos");
          }}
          onCall={startCall}
          onRetry={loadBusinesses}
          onDismissBanner={() => setBanner(null)}
        />
      )}

      {stage === "convos" && selId && (
        <ConversationsScreen
          biz={fallbackBiz(selId)}
          convos={convos[selId]}
          newIds={newIds}
          onBack={() => setStage("select")}
          onCall={() => startCall(selId)}
          onOpen={(convoId) => openConversation(selId, convoId, "convos")}
        />
      )}

      {stage === "transcript" && transcript && (
        <TranscriptScreen
          bizName={fallbackBiz(transcript.bizId).name}
          convo={detail}
          onBack={() => setStage(transcript.returnTo)}
          onContinue={() => startCall(transcript.bizId, transcript.convoId)}
        />
      )}

      {stage === "connecting" && <ConnectingScreen biz={fallbackBiz(callBizId)} onCancel={cancelCall} />}

      {stage === "call" && (
        <CallScreen biz={fallbackBiz(callBizId)} call={call} onPttDown={voice.pttDown} onPttUp={voice.pttUp}
          onEnd={voice.hangUp} />
      )}

      {stage === "ended" && (
        <EndedScreen
          biz={fallbackBiz(callBizId)}
          call={call}
          onRead={() => callBizId && call.conversationId && openConversation(callBizId, call.conversationId, "convos")}
          onBack={backToConversations}
        />
      )}
    </div>
  );
}
