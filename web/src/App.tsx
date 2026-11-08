import { useEffect, useState } from "react";
import { IDENTITIES } from "./api";
import { TripList } from "./TripList";
import { TripPage } from "./TripPage";
import { NoticeView, type Notice } from "./ui";

export { POLL_MS } from "./api";

const STORAGE_KEY = "identity";

export function App() {
  const [identity, setIdentity] = useState(() => {
    const saved = sessionStorage.getItem(STORAGE_KEY);
    return saved && IDENTITIES.includes(saved) ? saved : "ana";
  });
  // The open trip is the hash #/trips/<id>. No hash is the trip list.
  const [hash, setHash] = useState(window.location.hash);
  const [notice, setNotice] = useState<Notice | null>(null);

  useEffect(() => {
    const onHash = () => setHash(window.location.hash);
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);
  useEffect(() => {
    setNotice(null);
  }, [hash]);

  const tripId = /^#\/trips\/([^/]+)$/.exec(hash)?.[1];
  return (
    <main>
      <h1>Travel Planner</h1>
      <select
        aria-label="Identity"
        value={identity}
        onChange={(event) => {
          sessionStorage.setItem(STORAGE_KEY, event.target.value);
          setNotice(null);
          setIdentity(event.target.value);
        }}
      >
        {IDENTITIES.map((name) => (
          <option key={name} value={name}>{name}</option>
        ))}
      </select>
      <NoticeView notice={notice} />
      {tripId ? (
        <TripPage key={tripId} tripId={tripId} identity={identity} notify={setNotice} />
      ) : (
        <TripList identity={identity} notify={setNotice} />
      )}
    </main>
  );
}

export default App;
