// Stroke icons from the design (decorative: aria-hidden; buttons carry the text or aria-label).

import type { ReactNode } from "react";

function Icon({ size, stroke = 2, color = "currentColor", children }: {
  size: number; stroke?: number; color?: string; children: ReactNode;
}) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke={color} strokeWidth={stroke}
      strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      {children}
    </svg>
  );
}

const MIC = (
  <>
    <path d="M12 2a3 3 0 0 0-3 3v7a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3z" />
    <path d="M19 10v2a7 7 0 0 1-14 0v-2" />
  </>
);
const PHONE =
  "M22 16.92v3a2 2 0 0 1-2.18 2 19.79 19.79 0 0 1-8.63-3.07 19.5 19.5 0 0 1-6-6 19.79 19.79 0 0 1-3.07-8.67A2 2 0 0 1 4.11 2h3a2 2 0 0 1 2 1.72c.13.96.36 1.9.7 2.81a2 2 0 0 1-.45 2.11L8.09 9.91a16 16 0 0 0 6 6l1.27-1.27a2 2 0 0 1 2.11-.45c.91.34 1.85.57 2.81.7A2 2 0 0 1 22 16.92z";
const CHAT = "M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z";

export const MicIcon = ({ size = 16, stroke = 2 }: { size?: number; stroke?: number }) => (
  <Icon size={size} stroke={stroke}>
    {MIC}
    <line x1="12" y1="19" x2="12" y2="22" />
  </Icon>
);
export const MicSmallIcon = () => <Icon size={11} stroke={2.4}>{MIC}</Icon>;
export const SearchIcon = () => (
  <Icon size={18}>
    <circle cx="11" cy="11" r="7" />
    <line x1="21" y1="21" x2="16.65" y2="16.65" />
  </Icon>
);
export const CheckIcon = ({ size = 13, stroke = 3.2 }: { size?: number; stroke?: number }) => (
  <Icon size={size} stroke={stroke}>
    <polyline points="20 6 9 17 4 12" />
  </Icon>
);
export const ChatIcon = ({ size = 15, stroke = 2 }: { size?: number; stroke?: number }) => (
  <Icon size={size} stroke={stroke}>
    <path d={CHAT} />
  </Icon>
);
export const ChevronRight = () => (
  <Icon size={16} color="#8A919D">
    <polyline points="9 18 15 12 9 6" />
  </Icon>
);
export const ChevronLeft = () => (
  <Icon size={22}>
    <polyline points="15 18 9 12 15 6" />
  </Icon>
);
export const PhoneIcon = ({ size = 20 }: { size?: number }) => (
  <Icon size={size}>
    <path d={PHONE} />
  </Icon>
);
export const GlobeIcon = ({ size = 20 }: { size?: number }) => (
  <Icon size={size}>
    <circle cx="12" cy="12" r="10" />
    <line x1="2" y1="12" x2="22" y2="12" />
    <path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z" />
  </Icon>
);
export const ClockIcon = () => (
  <Icon size={12} stroke={2.2}>
    <circle cx="12" cy="12" r="9" />
    <polyline points="12 7 12 12 15 14" />
  </Icon>
);
