// Voice app (DEC-38): the "Voice Chat Bot" design, working against the real backend —
// businesses and saved conversations from app/api/conversations.py, live calls over
// /voice/browser with hold-to-talk. Six screens, one at a time, like the design's stages.

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

  const startCall = (bizId: string) => {
    setBanner(null);
    setCallBizId(bizId);
    setSelId(bizId);
    setStage("connecting");
    voice.start(bizId).catch((err: unknown) => {
      setBanner(`Couldn’t start the call: ${errorText(err)}`);
      setStage("select");
    });
  };

  const cancelCall = () => {
    setStage("select");
    voice.hangUp();
  };

  // follow the call: live -> call screen; closed while connecting -> back with the reason;
  // ended -> summary, and the new conversation shows up (marked NEW) in the lists
  useEffect(() => {
    if (stage === "connecting" && call.phase === "live") setStage("call");
    if (stage === "connecting" && call.phase === "ended") {
      if (call.endReason) setBanner(`Couldn’t start the call: ${call.endReason}`);
      setStage("select");
    }
    if (stage === "call" && call.phase === "ended") {
      setStage("ended");
      if (call.conversationId) setNewIds((prev) => new Set(prev).add(call.conversationId!));
      loadBusinesses();
      if (callBizId) loadConvos(callBizId);
    }
  }, [stage, call.phase, call.endReason, call.conversationId, callBizId, loadBusinesses, loadConvos]);

  const all = businesses.data ?? [];
  const find = (id: string | null) => all.find((b) => b.id === id);
  const fallbackBiz = (id: string | null): Business => find(id) ?? { id: id ?? "", name: "Business", category: "", conversation_count: 0 };

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
          onContinue={() => startCall(transcript.bizId)}
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
          onRead={() => callBizId && call.conversationId && openConversation(callBizId, call.conversationId, "select")}
          onBack={() => setStage("select")}
        />
      )}
    </div>
  );
}
