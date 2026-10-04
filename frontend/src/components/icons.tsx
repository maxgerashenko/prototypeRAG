// Icons ported as inline SVG/JSX (never dangerouslySetInnerHTML) from the SVG/icon
// constants in web/mic-test.html -- same paths, sizes and stroke widths.
import type { CSSProperties, JSX } from "react";

interface SvgProps {
  size: number;
  strokeWidth?: number;
  style?: CSSProperties;
  children: JSX.Element | JSX.Element[];
}

function Svg({ size, strokeWidth = 2, style, children }: SvgProps) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={strokeWidth}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      style={style}
    >
      {children}
    </svg>
  );
}

function MicPaths() {
  return (
    <>
      <path d="M12 2a3 3 0 0 0-3 3v7a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3z" />
      <path d="M19 10v2a7 7 0 0 1-14 0v-2" />
    </>
  );
}
function PhonePath() {
  return (
    <path d="M22 16.92v3a2 2 0 0 1-2.18 2 19.79 19.79 0 0 1-8.63-3.07 19.5 19.5 0 0 1-6-6 19.79 19.79 0 0 1-3.07-8.67A2 2 0 0 1 4.11 2h3a2 2 0 0 1 2 1.72c.13.96.36 1.9.7 2.81a2 2 0 0 1-.45 2.11L8.09 9.91a16 16 0 0 0 6 6l1.27-1.27a2 2 0 0 1 2.11-.45c.91.34 1.85.57 2.81.7A2 2 0 0 1 22 16.92z" />
  );
}
function ChatPath() {
  return <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />;
}

export function MicHeaderIcon() {
  return (
    <Svg size={16}>
      <MicPaths />
      <line x1="12" y1="19" x2="12" y2="22" />
    </Svg>
  );
}
export function MicIcon() {
  return (
    <Svg size={22}>
      <MicPaths />
      <line x1="12" y1="19" x2="12" y2="22" />
    </Svg>
  );
}
export function MicSmallIcon() {
  return (
    <Svg size={11} strokeWidth={2.4}>
      <MicPaths />
    </Svg>
  );
}
export function SearchIcon() {
  return (
    <Svg size={18}>
      <circle cx="11" cy="11" r="7" />
      <line x1="21" y1="21" x2="16.65" y2="16.65" />
    </Svg>
  );
}
export function CheckIcon() {
  return (
    <Svg size={13} strokeWidth={3.2} style={{ animation: "vw-pop .28s ease-out" }}>
      <polyline points="20 6 9 17 4 12" />
    </Svg>
  );
}
export function CheckBigIcon() {
  return (
    <Svg size={36} strokeWidth={2.6}>
      <polyline points="20 6 9 17 4 12" />
    </Svg>
  );
}
export function Chat15Icon() {
  return (
    <Svg size={15}>
      <ChatPath />
    </Svg>
  );
}
export function Chat12Icon() {
  return (
    <Svg size={12} strokeWidth={2.2}>
      <ChatPath />
    </Svg>
  );
}
export function Chat24Icon() {
  return (
    <Svg size={24}>
      <ChatPath />
    </Svg>
  );
}
export function ChevronIcon() {
  return (
    <Svg size={16} style={{ flexShrink: 0, color: "#8A919D" }}>
      <polyline points="9 18 15 12 9 6" />
    </Svg>
  );
}
export function BackIcon() {
  return (
    <Svg size={22}>
      <polyline points="15 18 9 12 15 6" />
    </Svg>
  );
}
export function ClockIcon() {
  return (
    <Svg size={12} strokeWidth={2.2}>
      <circle cx="12" cy="12" r="9" />
      <polyline points="12 7 12 12 15 14" />
    </Svg>
  );
}
export function Phone22Icon() {
  return (
    <Svg size={22}>
      <PhonePath />
    </Svg>
  );
}
export function Phone20Icon() {
  return (
    <Svg size={20}>
      <PhonePath />
    </Svg>
  );
}
export function Phone40Icon() {
  return (
    <Svg size={40}>
      <PhonePath />
    </Svg>
  );
}
export function HangupIcon() {
  return (
    <Svg size={26} style={{ transform: "rotate(135deg)" }}>
      <PhonePath />
    </Svg>
  );
}
