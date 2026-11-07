// Keeps a Twilio Voice SDK Device signed in past the access token's 1 h TTL (#213).
// The Device emits "tokenWillExpire" ~10 s before expiry; we fetch a fresh token and hand it
// over with updateToken(). Without this, the page's one long-lived Device fails with error
// 20104 on the first call (or incoming leg) after the hour. A failed refresh is only logged:
// the Device then expires, its next connect() throws, and onCall() drops it and re-creates it.

export interface TokenDevice {
  on(event: "tokenWillExpire", listener: () => void): unknown;
  updateToken(token: string): void;
}

export function keepTokenFresh(
  device: TokenDevice,
  getToken: () => Promise<{ token: string }>,
  log: (text: string) => void,
): void {
  device.on("tokenWillExpire", () => {
    getToken()
      .then(({ token }) => {
        device.updateToken(token);
        log("Voice token refreshed.");
      })
      .catch((err: unknown) => {
        log("Could not refresh the Voice token: " + (err instanceof Error ? err.message : String(err)));
      });
  });
}
