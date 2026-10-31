import { useState } from "react";
import type { TwilioNumbers } from "../api";
import type { Loadable } from "./App";
import { CloseIcon, SearchIcon } from "./icons";
import { fmtPhone } from "./format";
import { assignLabel, canAssign, filterNumbers, numberSubtitle } from "./settings";

interface Props {
  bizId: string;
  currentNumber: string | null;
  numbers: Loadable<TwilioNumbers>;
  assigning: boolean;
  error: string | null;
  onAssign: (number: string) => void;
  onClose: () => void;
}

/** Bottom sheet that lists the Twilio account's numbers; numbers another business holds are shown with "Move" (DEC-52). */
export function NumberSheet({ bizId, currentNumber, numbers, assigning, error, onAssign, onClose }: Props) {
  const [query, setQuery] = useState("");
  const [picked, setPicked] = useState<string | null>(null);
  const list = numbers.data?.numbers ?? [];
  const shown = filterNumbers(list, query, bizId);
  const pick = list.find((n) => n.number === picked) ?? null;
  const close = () => {
    if (!assigning) onClose();
  };
  const loading = numbers.status === "loading" && !numbers.data;
  const failure = numbers.status === "error" ? numbers.error : numbers.data?.error;

  return (
    <div className="sheet" data-testid="number-sheet">
      <button className="sheet-scrim" aria-label="Close number picker" onClick={close} />
      <div className="sheet-panel" role="dialog" aria-label="Choose a phone number">
        <div className="sheet-grip" />
        <div className="sheet-head">
          <div>
            <h2>Choose a number</h2>
            <p>Numbers in your Twilio account</p>
          </div>
          <button className="sheet-close" aria-label="Close" onClick={close}>
            <CloseIcon />
          </button>
        </div>
        <label className="sheet-filter">
          <SearchIcon />
          <input aria-label="Filter by area code or name" placeholder="Area code or name" value={query}
            onChange={(e) => setQuery(e.target.value)} />
        </label>
        <div className="sheet-list">
          {loading && <p className="sheet-empty">Loading numbers…</p>}
          {failure && <p className="sheet-empty" role="alert">Couldn’t load numbers ({failure}).</p>}
          {shown.map((n) => {
            const on = n.number === picked;
            const mine = n.business_id === bizId;
            const current = n.number === currentNumber;
            const takenBy = n.business_id && !mine ? n.business_name ?? "another business" : null;
            return (
              <button key={n.number} className={`num-opt${on ? " on" : ""}`} aria-pressed={on} disabled={current || (assigning && !on)}
                onClick={() => setPicked(n.number)}>
                <div className="num-radio">{on && <i />}</div>
                <div className="num-opt-text">
                  <span className="set-mono num-main">{fmtPhone(n.number)}</span>
                  <span className="num-opt-sub">{numberSubtitle(n)}</span>
                  {current && <span className="num-opt-sub">Current number</span>}
                  {takenBy && <span className="num-taken">Used by {takenBy}</span>}
                  {!n.routed && <span className="num-opt-sub">Not routed to this app</span>}
                </div>
                <div className="cap-chips">
                  {n.voice && <span className="cap-chip">Voice</span>}
                  {n.sms && <span className="cap-chip">SMS</span>}
                </div>
              </button>
            );
          })}
          {!loading && !failure && shown.length === 0 && <p className="sheet-empty">No numbers match.</p>}
        </div>
        {error && <p className="set-error" role="alert">{error}</p>}
        <button className={`sheet-go${assigning ? " busy" : ""}`} disabled={!canAssign(pick, currentNumber, assigning)}
          onClick={() => pick && onAssign(pick.number)}>
          {assigning && <span className="spin xl" />}
          <span>{assignLabel(pick, currentNumber, assigning)}</span>
        </button>
      </div>
    </div>
  );
}
