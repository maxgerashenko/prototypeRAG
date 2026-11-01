// Data of the Business settings screen (greeting, top questions, Twilio numbers) for the open
// business. The greeting loads as soon as a business is open (the card's row shows its state);
// the questions when the screen is on; the numbers when the picker opens. Every request has a
// counter, so an answer for a business or a screen the user already left is dropped.

import { useCallback, useEffect, useRef, useState } from "react";
import {
  assignNumber,
  getGreeting,
  getTopQuestions,
  listTwilioNumbers,
  saveGreeting,
  type AssignResult,
  type GreetingStatus,
  type TopQuestions,
  type TwilioNumbers,
} from "../api";
import type { Loadable } from "./App";

const errorText = (err: unknown) => (err instanceof Error ? err.message : String(err));

/** What the settings screen needs from the hook (everything but the business and Back). */
export interface SettingsBindings {
  greeting: Loadable<GreetingStatus>;
  savingGreeting: boolean;
  greetingError: string | null;
  questions: Loadable<TopQuestions>;
  numbers: Loadable<TwilioNumbers>;
  sheetOpen: boolean;
  assigning: boolean;
  assignError: string | null;
  onSaveGreeting: (text: string) => void;
  onOpenSheet: () => void;
  onCloseSheet: () => void;
  onAssign: (number: string) => void;
  /** Pull-to-refresh: refetch in place (the old data stays on screen), resolved when done. */
  onRefresh: () => Promise<unknown>;
}

export function useBusinessSettings(
  bizId: string | null,
  active: boolean,
  onAssigned: (result: AssignResult) => void,
) {
  const [greeting, setGreeting] = useState<Loadable<GreetingStatus>>({ status: "loading" });
  const [savingGreeting, setSaving] = useState(false);
  const [greetingError, setGreetingError] = useState<string | null>(null);
  const [questions, setQuestions] = useState<Loadable<TopQuestions>>({ status: "loading" });
  const [numbers, setNumbers] = useState<Loadable<TwilioNumbers>>({ status: "loading" });
  const [sheetOpen, setSheetOpen] = useState(false);
  const [assigning, setAssigning] = useState(false);
  const [assignError, setAssignError] = useState<string | null>(null);
  const greetingReq = useRef(0);
  const questionsReq = useRef(0);
  const numbersReq = useRef(0);
  const assignReq = useRef(0);

  // another business: forget the previous one's state, load its greeting
  useEffect(() => {
    const req = ++greetingReq.current;
    ++assignReq.current;
    setGreeting({ status: "loading" });
    setSaving(false);
    setGreetingError(null);
    setSheetOpen(false);
    setAssigning(false);
    setAssignError(null);
    if (!bizId) return;
    getGreeting(bizId).then(
      (data) => req === greetingReq.current && setGreeting({ status: "ok", data }),
      (err) => req === greetingReq.current && setGreeting({ status: "error", error: errorText(err) }),
    );
  }, [bizId]);

  // the screen is on: fetch the questions (again on every visit)
  useEffect(() => {
    const req = ++questionsReq.current;
    if (!bizId || !active) return;
    setQuestions({ status: "loading" });
    getTopQuestions(bizId).then(
      (data) => req === questionsReq.current && setQuestions({ status: "ok", data }),
      (err) => req === questionsReq.current && setQuestions({ status: "error", error: errorText(err) }),
    );
  }, [bizId, active]);

  // the picker opens: fetch the account's numbers (again every time: another business may hold them now)
  useEffect(() => {
    const req = ++numbersReq.current;
    if (!sheetOpen) return;
    setNumbers({ status: "loading" });
    listTwilioNumbers().then(
      (data) => req === numbersReq.current && setNumbers({ status: "ok", data }),
      (err) => req === numbersReq.current && setNumbers({ status: "error", error: errorText(err) }),
    );
  }, [sheetOpen]);

  const onSaveGreeting = useCallback(
    (text: string) => {
      if (!bizId) return;
      const req = ++greetingReq.current;
      setSaving(true);
      setGreetingError(null);
      saveGreeting(bizId, text).then(
        (data) => {
          if (req !== greetingReq.current) return;
          setGreeting({ status: "ok", data });
          setGreetingError(data.cache_error ? `Saved, but the audio could not be cached (${data.cache_error}).` : null);
          setSaving(false);
        },
        (err) => {
          if (req !== greetingReq.current) return;
          setGreetingError(errorText(err)); // the draft stays in the field, the old greeting stays shown
          setSaving(false);
        },
      );
    },
    [bizId],
  );

  const onOpenSheet = useCallback(() => {
    setAssignError(null);
    setSheetOpen(true);
  }, []);

  const onCloseSheet = useCallback(() => {
    if (!assigning) setSheetOpen(false);
  }, [assigning]);

  const onAssign = useCallback(
    (number: string) => {
      if (!bizId) return;
      const req = ++assignReq.current;
      setAssigning(true);
      setAssignError(null);
      assignNumber(bizId, number).then(
        (result) => {
          onAssigned(result); // the server moved the number, whichever business is open now
          if (req !== assignReq.current) return;
          setAssigning(false);
          setSheetOpen(false);
        },
        (err) => {
          if (req !== assignReq.current) return;
          setAssigning(false);
          setAssignError(errorText(err));
        },
      );
    },
    [bizId, onAssigned],
  );

  const onRefresh = useCallback(() => {
    if (!bizId) return Promise.resolve();
    const g = ++greetingReq.current;
    const q = ++questionsReq.current;
    return Promise.allSettled([
      getGreeting(bizId).then(
        (data) => g === greetingReq.current && setGreeting({ status: "ok", data }),
        (err) => g === greetingReq.current && !greeting.data && setGreeting({ status: "error", error: errorText(err) }),
      ),
      getTopQuestions(bizId).then(
        (data) => q === questionsReq.current && setQuestions({ status: "ok", data }),
        (err) => q === questionsReq.current && !questions.data && setQuestions({ status: "error", error: errorText(err) }),
      ),
    ]);
  }, [bizId, greeting.data, questions.data]);

  const bindings: SettingsBindings = {
    greeting, savingGreeting, greetingError, questions, numbers, sheetOpen, assigning, assignError,
    onSaveGreeting, onOpenSheet, onCloseSheet, onAssign, onRefresh,
  };
  return { ...bindings, greetingCached: greeting.data ? greeting.data.cached : null };
}
