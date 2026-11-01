import { useEffect, useRef, useState } from "react";
import type { SessionItem } from "../api";
import {
  SESSION_DEFAULT_MAX, SESSION_MAX_ATTENDEES, SESSION_NAME_MAX, bookedLine, dayTitle, hourRange, isClosedHour, isFull,
} from "./bookings";
import { CloseIcon, MicSmallIcon, PeopleIcon, PhoneIcon } from "./icons";
import { fmtPhone } from "./format";
import { StatusChip } from "./StatusChip";
import { useSheetDrag } from "./useSheetDrag";
import { rowDecision, rowFires, rowOffset } from "./rowSwipe";

interface Common {
  day: string;
  todayIso: string;
  hoursOpen: { open: number | null; close: number | null }; // opening hours (null = unknown)
  busy: boolean;
  error: string | null;
  onClose: () => void;
}

interface Props extends Common {
  draft: { hour: number | null } | null;
  session: SessionItem | null;
  ended: boolean; // the session is over: read-only
  freeHourList: number[];
  onCreate: (v: { name: string; max_attendees: number; private: boolean; hour: number }) => void;
  onSave: (patch: { name?: string; max_attendees?: number; private?: boolean }, sessionId?: string) => void;
  onDelete: () => void;
  onRemove: (bookingId: string) => void;
  onOpenConversation: (conversationId: string) => void;
}

/** Bottom sheet to create a group session (draft) or to edit / remove callers from / delete one. */
export function SessionSheet(p: Props) {
  const rootRef = useRef<HTMLDivElement>(null);
  useSheetDrag(rootRef, { busy: p.busy, onClose: p.onClose }); // swipe the panel down (or drag its grip) to close
  return (
    <div className="sheet" data-testid="session-sheet" data-swipe-back="off" ref={rootRef}>
      <button className="sheet-scrim" aria-label="Close session" disabled={p.busy} onClick={p.onClose} />
      {p.session ? (
        <EditForm key={p.session.id} {...p} session={p.session} />
      ) : (
        <CreateForm {...p} />
      )}
    </div>
  );
}

/** Attendees 1-30 on a volume-style bar (people icon inside, number outside on the right, one stop per number) with a small
 *  Private switch beside it. Private (closed to the public: the assistant never books into it) keeps the limit. */
function SeatPicker({ seats, priv, min, onSeats, onPriv, disabled }: {
  seats: number; priv: boolean; min: number; onSeats: (n: number) => void; onPriv: (on: boolean) => void; disabled?: boolean;
}) {
  return (
    <div className="ss-seats">
      <div className={`ss-bar${disabled ? " disabled" : ""}`} style={{ "--r": (seats - 1) / (SESSION_MAX_ATTENDEES - 1) } as React.CSSProperties}>
        <i className="ss-bar-fill" aria-hidden="true" />
        <PeopleIcon />
        <input type="range" aria-label="Attendees" min={1} max={SESSION_MAX_ATTENDEES} step={1} value={seats}
          disabled={disabled} aria-valuetext={`${seats} attendees`}
          onChange={(e) => onSeats(Math.max(min, Number(e.target.value)))} />
      </div>
      <output className="ss-seats-value" aria-live="polite">{seats}</output>
      <button type="button" role="switch" aria-checked={priv} className={`ss-private${priv ? " on" : ""}`} disabled={disabled}
        title="Private: the assistant will not offer it to callers; only people you add" onClick={() => onPriv(!priv)}>
        <span className="ss-private-text">Private</span>
        <span className="ss-switch" aria-hidden="true" />
      </button>
    </div>
  );
}

function CreateForm(p: Props) {
  const first = p.freeHourList[0] ?? null;
  const [hour, setHour] = useState<number | null>(p.draft?.hour ?? first);
  const [name, setName] = useState("");
  const [seats, setSeats] = useState(SESSION_DEFAULT_MAX);
  const [priv, setPriv] = useState(false);
  const none = p.freeHourList.length === 0;
  // keep the preselected hour in the list even if it is not (anymore) free, so the select shows it
  const options = hour !== null && !p.freeHourList.includes(hour) ? [hour, ...p.freeHourList] : p.freeHourList;
  const canSubmit = !p.busy && !none && hour !== null && name.trim() !== "";
  return (
    <form className="sheet-panel ss-panel" role="dialog" aria-modal="true" aria-label="New session"
      onSubmit={(e) => {
        e.preventDefault();
        if (canSubmit && hour !== null) p.onCreate({ name: name.trim(), max_attendees: seats, private: priv, hour });
      }}>
      <div className="sheet-grip" />
      <div className="sheet-head">
        <div>
          <h2>New session</h2>
          <p>{dayTitle(p.day, p.todayIso)}</p>
        </div>
      </div>

      <div className="ss-scroll">
        <label className="ss-field">
          <span>Starts</span>
          {none ? (
            <p className="ss-none">No free hours left today</p>
          ) : (
            <select value={hour ?? ""} onChange={(e) => setHour(Number(e.target.value))}>
              {options.map((h) => <option key={h} value={h}>{hourRange(h)}</option>)}
            </select>
          )}
        </label>
        {!none && hour !== null && isClosedHour(hour, p.hoursOpen) && <p className="ss-warn">Outside opening hours</p>}

        <label className="ss-field">
          <span>Name</span>
          <input value={name} maxLength={SESSION_NAME_MAX} placeholder="e.g. Sauna social" enterKeyHint="go"
            autoCapitalize="sentences" onChange={(e) => setName(e.target.value)} />
        </label>

        <div className="ss-field">
          <span>Attendees</span>
          <SeatPicker seats={seats} priv={priv} min={1} onSeats={setSeats} onPriv={setPriv} />
        </div>

        {p.error && <p className="set-error ss-error" role="alert">{p.error}</p>}
        <button type="submit" className="ss-primary" disabled={!canSubmit}>Add session</button>
      </div>
    </form>
  );
}

function EditForm(p: Props & { session: SessionItem }) {
  const s = p.session;
  const panelRef = useRef<HTMLDivElement>(null);
  useEffect(() => panelRef.current?.focus(), []); // no close button: the scrim, swipe-down, Esc and Back close
  const [name, setName] = useState(s.name);
  const [seats, setSeats] = useState(s.max_attendees);
  const [priv, setPriv] = useState(s.private);
  const [removing, setRemoving] = useState<string | null>(null);
  const [deleting, setDeleting] = useState(false);
  const h = Math.floor(s.start_min / 60);
  const google = s.source === "google"; // time and name come from Google Calendar; seats and Private are ours
  // no Save button: a change is saved 2 s after the last edit, and when the sheet closes (any way)
  const patch: { name?: string; max_attendees?: number; private?: boolean } = {};
  if (!google && name.trim() !== s.name) patch.name = name.trim();
  if (seats !== s.max_attendees) patch.max_attendees = seats;
  if (priv !== s.private) patch.private = priv;
  const pending = !p.ended && name.trim() !== "" && Object.keys(patch).length > 0 ? patch : null;
  const latest = useRef({ pending, onSave: p.onSave, skip: false });
  latest.current = { pending, onSave: p.onSave, skip: latest.current.skip };
  const patchKey = JSON.stringify(pending);
  // "Saved" shows once the server has caught up with an edit, and hides again as soon as something is changed
  const [saved, setSaved] = useState(false);
  const wasPending = useRef(false);
  useEffect(() => {
    if (pending) setSaved(false);
    else if (wasPending.current && !p.error) setSaved(true);
    wasPending.current = !!pending;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [patchKey]);
  useEffect(() => {
    if (!pending || p.busy) return;
    const t = window.setTimeout(() => p.onSave(pending, s.id), 2000);
    return () => window.clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [patchKey, p.busy]);
  useEffect(() => () => {
    const l = latest.current;
    if (l.pending && !l.skip) l.onSave(l.pending, s.id);
  }, [s.id]);
  return (
    <div className="sheet-panel ss-panel" role="dialog" aria-modal="true" aria-label="Session" tabIndex={-1} ref={panelRef}>
      <div className="sheet-grip" />
      <div className="sheet-head">
        <div>
          <h2>Session</h2>
          <p className="ss-meta">
            <span>{hourRange(h)} · {bookedLine(s)}</span>
            <span className="ss-tags">
              {pending
                ? <StatusChip kind={p.busy ? "saving" : "dirty"} label={p.busy ? "Saving\u2026" : "Unsaved changes"} testId="session-chip" />
                : saved && <StatusChip kind="saved" label="Saved" testId="session-chip" />}
              {google && <span className="ss-chip" data-testid="session-google">Google Calendar</span>}
              {p.ended && <span className="ss-chip">Ended</span>}
              {isFull(s) && <span className="ss-chip full">FULL</span>}
            </span>
          </p>
        </div>
      </div>

      <div className="ss-scroll">
        <label className="ss-field">
          <span>Name</span>
          <input value={name} maxLength={SESSION_NAME_MAX} readOnly={p.ended || google} enterKeyHint="done"
            autoCapitalize="sentences" onChange={(e) => setName(e.target.value)} />
        </label>
        <div className="ss-field">
          <span>Attendees</span>
          <SeatPicker seats={seats} priv={priv} min={Math.max(1, s.booked)} onSeats={setSeats} onPriv={setPriv} disabled={p.ended} />
        </div>

        <div className="ss-field ss-attendees">
          <span>Booked ({s.booked}/{s.max_attendees})</span>
          {s.attendees.length === 0 ? (
            <p className="ss-none">No one has booked yet.</p>
          ) : (
            <ul className="ss-list">
              {s.attendees.map((a) => (
                <AttendeeRow key={a.id} a={a} busy={p.busy} ended={p.ended} confirming={removing === a.id}
                  onOpen={a.conversation_id ? () => p.onOpenConversation(a.conversation_id!) : a.phone ? () => { window.location.href = `tel:${a.phone}`; } : null} onAsk={() => setRemoving(a.id)} onKeep={() => setRemoving(null)}
                  onRemove={() => { p.onRemove(a.id); setRemoving(null); }} />
              ))}
            </ul>
          )}
        </div>
      </div>

      {p.error && <p className="set-error ss-error" role="alert">{p.error}</p>}

      {!p.ended && (
        <div className="ss-footer">
          {deleting ? (
            <div className="ss-confirm">
              <span>
                {s.booked > 0
                  ? `Delete this session? ${s.booked} ${s.booked === 1 ? "caller" : "callers"} will be cancelled and texted.`
                  : "Delete this session?"}
              </span>
              <div className="ss-confirm-btns">
                <button type="button" className="ss-btn" disabled={p.busy} onClick={() => setDeleting(false)}>Keep</button>
                <button type="button" className="ss-btn danger fill" disabled={p.busy} onClick={() => { latest.current.skip = true; p.onDelete(); }}>Delete</button>
              </div>
            </div>
          ) : google ? (
            <p className="ss-none" data-testid="session-google-hint">From Google Calendar. Move or delete it there; this page updates on refresh.</p>
          ) : (
            <button type="button" className="ss-delete" disabled={p.busy} onClick={() => setDeleting(true)}>Delete session</button>
          )}
        </div>
      )}
    </div>
  );
}

/** One attendee. Swipe left (touch or mouse) to reveal Remove; dragged fully open and released, it removes.
 *  A visually hidden Remove button (shown on focus) gives keyboard users the same action, with a confirmation. */
function AttendeeRow(p: {
  a: SessionItem["attendees"][number]; busy: boolean; ended: boolean; confirming: boolean;
  onOpen: (() => void) | null; onAsk: () => void; onKeep: () => void; onRemove: () => void;
}) {
  const { a } = p;
  const [off, setOff] = useState(0);
  const [dragging, setDragging] = useState(false);
  const g = useRef<{ x: number; y: number; id: number; swiping: boolean } | null>(null);
  const moved = useRef(false);
  const can = !p.busy && !p.ended && !p.confirming;
  const canOpen = !!p.onOpen && !p.busy; // read-only rows still open the conversation / call
  const done = (fire: boolean) => {
    g.current = null;
    setDragging(false);
    setOff(0);
    if (fire) p.onAsk(); // a full swipe asks Keep / Remove, like the x
  };
  return (
    <li className="ss-row" data-testid="attendee-row">
      {p.confirming ? (
        <div className="ss-confirm" role="group" aria-label={`Remove ${a.name}?`}>
          <div className="ss-confirm-btns">
            <button type="button" className="ss-btn" disabled={p.busy} onClick={p.onKeep}>Keep</button>
            <button type="button" className="ss-btn danger" disabled={p.busy} onClick={p.onRemove}>Remove</button>
          </div>
        </div>
      ) : (
        <>
          <div className={`ss-slide${can ? " swipeable" : ""}${dragging ? " dragging" : ""}${canOpen ? " openable" : ""}`} data-testid="attendee-slide"
            role={canOpen ? "button" : undefined} tabIndex={canOpen ? 0 : undefined}
            aria-label={canOpen ? (a.conversation_id ? `Open conversation with ${a.name}` : `Call ${a.name}`) : undefined}
            onClick={(e) => { if (canOpen && !(e.target as Element).closest("a, button")) p.onOpen?.(); }}
            onKeyDown={(e) => {
              if (canOpen && e.target === e.currentTarget && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); p.onOpen?.(); }
            }}
            onDragStart={(e) => e.preventDefault()}
            onClickCapture={(e) => { if (moved.current) { e.preventDefault(); e.stopPropagation(); moved.current = false; } }}
            onPointerDown={(e) => {
              if (!can || !e.isPrimary) return;
              moved.current = false;
              g.current = { x: e.clientX, y: e.clientY, id: e.pointerId, swiping: false };
            }}
            onPointerMove={(e) => {
              const s = g.current;
              if (!s || e.pointerId !== s.id) return;
              const dx = e.clientX - s.x;
              if (!s.swiping) {
                const d = rowDecision(dx, e.clientY - s.y);
                if (d === "none") { g.current = null; return; }
                if (d === "wait") return;
                s.swiping = true;
                moved.current = true;
                setDragging(true);
                try { e.currentTarget.setPointerCapture(e.pointerId); } catch { /* fine */ }
              }
              setOff(rowOffset(dx));
            }}
            onPointerUp={(e) => {
              const s = g.current;
              if (!s || e.pointerId !== s.id) return;
              done(s.swiping && rowFires(rowOffset(e.clientX - s.x)));
            }}
            onPointerCancel={() => { if (g.current) done(false); }}>
            <div className="ss-who">
              <span className="ss-who-name">
                <span>{a.name}</span>
              </span>
              {(a.phone || a.conversation_id) && (
                <div className="ss-line">
                  {a.phone
                    ? <a className={`ss-phone${a.conversation_id ? " conv" : ""}`} href={`tel:${a.phone}`}>
                        {a.conversation_id ? <MicSmallIcon /> : <PhoneIcon size={12} />}{fmtPhone(a.phone)}
                      </a>
                    : <span className="ss-phone conv"><MicSmallIcon /></span>}
                  <span className="ss-sep" aria-hidden="true">·</span><span className={`ss-hint${a.conversation_id ? " conv" : ""}`}>{a.conversation_id ? "See conversation" : "Make a call"}</span>
                </div>
              )}
            </div>
            {!p.ended && (
              <button type="button" className="ss-x" aria-label={`Remove ${a.name}`} disabled={p.busy} onClick={p.onAsk}><span><CloseIcon size={14} stroke={1.8} /></span></button>
            )}
          </div>
          <div className={`ss-reveal${dragging ? " dragging" : ""}`} aria-hidden="true" data-testid="attendee-reveal" style={{ width: off }}><span>Remove</span></div>
        </>
      )}
    </li>
  );
}
