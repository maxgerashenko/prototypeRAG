import type { BusinessOut } from "../api/types";
import { Phone40Icon } from "../components/icons";

export function ConnectingScreen({ biz, onCancel }: { biz: BusinessOut | null; onCancel: () => void }) {
  return (
    <div className="connecting-screen">
      <div className="connecting-orb">
        <div className="connecting-orb__ring" />
        <div className="connecting-orb__ring connecting-orb__ring--delayed" />
        <div className="connecting-orb__core">
          <Phone40Icon />
        </div>
      </div>
      <div className="connecting-text">
        <div className="connecting-text__title">Connecting…</div>
        <div className="connecting-text__sub">Starting a call about {biz ? biz.name : ""}</div>
      </div>
      <button className="connecting-cancel-btn" onClick={onCancel}>
        Cancel
      </button>
    </div>
  );
}
