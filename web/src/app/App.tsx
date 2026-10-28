// Voice app (DEC-42): the "Voice Chat Bot" design, working against the real backend —
// businesses (app/api/businesses.py) and saved conversations (app/api/conversations.py),
// live calls over /voice/browser with server push-to-talk. Six screens, one at a time, like
// the design's stages. Keyboard and error handling follow the earlier plain-HTML version of
// this app (main's web/mic-test.html): Space calls / holds to talk, Esc backs out or ends
// the call, Backspace ends a call, "Unknown business" returns to the list.
// Phone calls the system takes (Twilio, DEC-46) show up live on the same call screen,
// watch-only: the app switches to one when it starts, unless the user is on a call here.

import { useCallback, useEffect, useRef, useState } from "react";
import {
  getConversation,
  getMainNumber,
  listBusinesses,
  listConversations,
  setMainNumber,
  type Business,
  type ConversationDetail,
  type ConversationSummary,
} from "../api";
import { CallScreen, ConnectingScreen, EndedScreen, PhoneCallScreen } from "./CallScreens";
import { ConversationsScreen } from "./ConversationsScreen";
import { SelectScreen } from "./SelectScreen";
import { TranscriptScreen } from "./TranscriptScreen";
import { useVoiceCall } from "./useVoiceCall";
import { PhoneIcon } from "./icons";
import { callerSpoke } from "./callState";
import { callerLabel, latestLive } from "./liveCalls";
import { useLiveCalls } from "./useLiveCalls";
import { vibrateTick } from "./pullToRefresh";
import { PullRegion } from "./PullIndicator";
import { usePullToRefresh } from "./usePullToRefresh";

export interface Loadable<T> {
  status: "loading" | "ok" | "error";
  data?: T;
  error?: string;
}

type Stage = "select" | "convos" | "transcript" | "connecting" | "call" | "ended" | "phone" | "phoneEnded";

interface TranscriptRef {
  bizId: string;
  convoId: string;
  returnTo: "select" | "convos";
}

/** The picker's pull-to-refresh: a full reload (its own function so e2e can watch the navigation). */
const reloadPage = () => {
  window.location.reload();
};

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
  const [mainNumber, setMainNumberState] = useState<string | null>(null);
  const voice = useVoiceCall();
  const call = voice.state;
  const detailReq = useRef(0);
  const phoneCalls = useLiveCalls();
  const [phoneId, setPhoneId] = useState<string | null>(null); // the phone call on screen
  const [phoneReturn, setPhoneReturn] = useState<Stage>("select"); // where Close goes
  const shownPhone = useRef(new Set<string>()); // calls already switched to once
  const phone = phoneId ? phoneCalls[phoneId] : undefined;

  const loadBusinesses = useCallback(() => {
    setBusinesses((prev) => ({ ...prev, status: "loading" }));
    return listBusinesses().then(
      (data) => setBusinesses({ status: "ok", data }),
      (err) => setBusinesses((prev) => ({ ...prev, status: "error", error: errorText(err) })),
    );
  }, []);

  const loadConvos = useCallback((bizId: string) => {
    setConvos((prev) => ({ ...prev, [bizId]: { ...prev[bizId], status: "loading" } }));
    return listConversations(bizId).then(
      (data) => setConvos((prev) => ({ ...prev, [bizId]: { status: "ok", data } })),
      (err) => setConvos((prev) => ({ ...prev, [bizId]: { ...prev[bizId], status: "error", error: errorText(err) } })),
    );
  }, []);

  useEffect(() => {
    loadBusinesses();
  }, [loadBusinesses]);
  useEffect(() => {
    getMainNumber().then((r) => setMainNumberState(r.number), () => setMainNumberState(null));
  }, []);

  // picking a business also points the one main number at it (taken off whichever had it),
  // so dialling the number from a phone reaches the business on screen
  const assignMainNumber = (bizId: string) => {
    if (!mainNumber || find(bizId)?.phone_numbers.includes(mainNumber)) return;
    setMainNumber(bizId, true).then(
      () => setBusinesses((prev) => ({
        ...prev,
        data: prev.data?.map((b) => {
          const rest = b.phone_numbers.filter((n) => n !== mainNumber);
          return { ...b, phone_numbers: b.id === bizId ? [...rest, mainNumber] : rest };
        }),
      })),
      (err) => setBanner(`Couldn’t assign ${mainNumber}: ${errorText(err)}`),
    );
  };

  const openConversation = (bizId: string, convoId: string, returnTo: TranscriptRef["returnTo"]) => {
    const req = ++detailReq.current;
    // Opening a conversation counts as seeing it: drop its NEW mark however the user leaves.
    setNewIds((prev) => {
      if (!prev.has(convoId)) return prev;
      const next = new Set(prev);
      next.delete(convoId);
      return next;
    });
    setTranscript({ bizId, convoId, returnTo });
    setDetail({ status: "loading" });
    setStage("transcript");
    getConversation(bizId, convoId).then(
      (data) => req === detailReq.current && setDetail({ status: "ok", data }),
      (err) => req === detailReq.current && setDetail({ status: "error", error: errorText(err) }),
    );
  };

  // pull-to-refresh on the open transcript: refetch in place, the old data stays on screen
  const refreshDetail = () => {
    if (!transcript) return Promise.resolve();
    const req = ++detailReq.current;
    setDetail((prev) => ({ ...prev, status: "loading" }));
    return getConversation(transcript.bizId, transcript.convoId).then(
      (data) => req === detailReq.current && setDetail({ status: "ok", data }),
      (err) => req === detailReq.current && setDetail((prev) => ({ ...prev, status: "error", error: errorText(err) })),
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

  // a phone call starts -> show it, once per call, unless a call of our own is on
  const live = latestLive(phoneCalls);
  useEffect(() => {
    if (!live || shownPhone.current.has(live.info.id)) return;
    if (stage === "connecting" || stage === "call" || stage === "phone") return;
    shownPhone.current.add(live.info.id);
    watchPhone(live.info.id);
  }, [live, stage]);

  // the watched phone call ended -> its summary; the new conversation shows up in the lists
  useEffect(() => {
    if (stage !== "phone" || !phone || phone.state.phase !== "ended") return;
    loadBusinesses();
    loadConvos(phone.info.business_id);
    const { conversationId } = phone.state;
    if (conversationId && callerSpoke(phone.state)) setNewIds((prev) => new Set(prev).add(conversationId));
    setStage("phoneEnded");
  }, [stage, phone, loadBusinesses, loadConvos]);

  const closePhone = () => setStage(phoneReturn);
  const watchPhone = (id: string) => {
    setPhoneReturn(stage === "phoneEnded" ? "select" : stage);
    setPhoneId(id);
    setStage("phone");
  };

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
      else if (stage === "phone" || stage === "phoneEnded") closePhone();
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

  // pull-to-refresh: the picker reloads the page; the lists and the transcript refetch in place.
  // Off on the call screens and while a phone call is in progress (never reload mid-call).
  // Not gated on call.phase: it starts as "connecting" before any call.
  const rootRef = useRef<HTMLDivElement>(null);
  const ptrEnabled =
    (stage === "select" || (stage === "convos" && !!selId) || (stage === "transcript" && !!transcript)) && !live;
  const ptr = usePullToRefresh(rootRef, {
    enabled: ptrEnabled,
    onRefresh: () => {
      if (stage === "select") return reloadPage();
      if (stage === "convos" && selId) return Promise.allSettled([loadBusinesses(), loadConvos(selId)]);
      if (stage === "transcript") return refreshDetail();
    },
    onArmed: () => {
      if (stage === "select") vibrateTick();
    },
  });

  const all = businesses.data ?? [];
  const find = (id: string | null) => all.find((b) => b.id === id);
  const fallbackBiz = (id: string | null): Business =>
    find(id) ?? { id: id ?? "", name: "Business", website: null, domain: null, conversation_count: 0, default_location: null, phone_numbers: [] };

  return (
    <div className="vapp" ref={rootRef}>
      {stage === "select" && (
        <>
        <div className="ptr-gap" />
        <PullRegion state={ptr}>
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
            if (id) {
              loadConvos(id);
              assignMainNumber(id);
            }
          }}
          onOpen={(bizId, convoId) => openConversation(bizId, convoId, "select")}
          onSeeAll={(bizId) => {
            setSelId(bizId);
            setStage("convos");
          }}
          onCall={startCall}
          mainNumber={mainNumber}
          onRetry={loadBusinesses}
          onDismissBanner={() => setBanner(null)}
        />
        </PullRegion>
        </>
      )}

      {stage === "convos" && selId && (
        <ConversationsScreen
          pull={ptr}
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
          pull={ptr}
          bizName={fallbackBiz(transcript.bizId).name}
          convo={detail}
          onBack={() => setStage(transcript.returnTo)}
          onContinue={() => startCall(transcript.bizId, transcript.convoId)}
        />
      )}

      {stage === "connecting" && <ConnectingScreen biz={fallbackBiz(callBizId)} onCancel={cancelCall} />}

      {stage === "call" && (
        <CallScreen biz={fallbackBiz(callBizId)} call={call} handsFree={voice.handsFree} onPttDown={voice.pttDown}
          onPttUp={voice.pttUp} onEnd={voice.hangUp} />
      )}

      {stage === "phone" && phone && (
        <PhoneCallScreen biz={fallbackBiz(phone.info.business_id)} call={phone.state}
          caller={callerLabel(phone.info.caller)} onClose={closePhone} />
      )}

      {stage === "phoneEnded" && phone && (
        <EndedScreen
          biz={fallbackBiz(phone.info.business_id)}
          call={phone.state}
          onRead={() => {
            if (!phone.state.conversationId) return;
            setSelId(phone.info.business_id);
            openConversation(phone.info.business_id, phone.state.conversationId, "convos");
          }}
          onBack={() => {
            setSelId(phone.info.business_id);
            setStage("convos");
          }}
        />
      )}

      {/* a phone call in progress that isn't on screen (closed, or one of our own calls was on):
          the web call button's look, with the caller's number */}
      {live && stage !== "phone" && stage !== "connecting" && stage !== "call" && (
        <div className="call-wrap phone-live">
          <div className="halo" />
          <button className="call-btn" data-testid="phone-live" onClick={() => watchPhone(live.info.id)}
            aria-label={`Phone call from ${callerLabel(live.info.caller)} about ${fallbackBiz(live.info.business_id).name}`}>
            <PhoneIcon size={22} />
            <span className="phone-live-text">
              <span className="mono-font">{callerLabel(live.info.caller)}</span>
              <small className="ellipsis">Phone call · {fallbackBiz(live.info.business_id).name}</small>
            </span>
          </button>
        </div>
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
