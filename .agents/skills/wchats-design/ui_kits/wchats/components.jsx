/* global React */
// Atomic components — Logo, icons, primitives.
// Exports to window at the end.

const { useEffect, useRef, useState } = React;

// ---- Icons (extracted from the prototype) -----------------------------
const Icon = ({ name, size = 16, strokeWidth = 2, ...rest }) => {
  const paths = {
    'chat-bubble': <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>,
    'arrow-right': <><line x1="5" y1="12" x2="19" y2="12"/><polyline points="12 5 19 12 12 19"/></>,
    'play': <polygon points="5 3 19 12 5 21 5 3"/>,
    'check': <polyline points="20 6 9 17 4 12"/>,
    'upload': <><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="17 8 12 3 7 8"/><line x1="12" y1="3" x2="12" y2="15"/></>,
    'settings': <><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 2 2 2 0 0 1-2-2v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1-2-2 2 2 0 0 1 2-2h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 2 2 2 2 0 0 1-2 2h-.09a1.65 1.65 0 0 0-1.51 1z"/></>,
    'bolt': <polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/>,
    'code': <><polyline points="16 18 22 12 16 6"/><polyline points="8 6 2 12 8 18"/></>,
    'shield-check': <><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><polyline points="22 4 12 14.01 9 11.01"/></>,
    'shield': <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/>,
    'clock': <><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></>,
    'user-check': <><path d="M16 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="8.5" cy="7" r="4"/><polyline points="17 11 19 13 23 9"/></>,
  };
  return (
    <svg
      width={size} height={size}
      viewBox="0 0 24 24"
      fill="none" stroke="currentColor"
      strokeWidth={strokeWidth} strokeLinecap="round" strokeLinejoin="round"
      {...rest}
    >
      {paths[name]}
    </svg>
  );
};

// ---- Logo -------------------------------------------------------------
const Logo = () => (
  <div className="nav-logo">
    <div className="logo-mark">
      <Icon name="chat-bubble" size={14} strokeWidth={2.5} />
    </div>
    <span className="logo-name">w<span className="logo-dot">.</span>chats</span>
  </div>
);

// ---- Button -----------------------------------------------------------
const Button = ({ variant = 'primary', size = 'md', children, onClick, ...rest }) => {
  const sizeClass = size === 'lg' ? ' btn-lg' : size === 'sm' ? ' btn-sm' : '';
  return (
    <button className={`btn btn-${variant}${sizeClass}`} onClick={onClick} {...rest}>
      {children}
    </button>
  );
};

// ---- Chip -------------------------------------------------------------
const Chip = ({ variant = 'live', pulse = true, children }) => (
  <span className={`chip chip-${variant}`}>
    <span className={`dot${pulse ? ' pulse' : ''}`}></span>
    {children}
  </span>
);

Object.assign(window, { Icon, Logo, Button, Chip });
