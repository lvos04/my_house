import type { SVGProps } from 'react';

const base = { width: 20, height: 20, viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', strokeWidth: 1.8, strokeLinecap: 'round', strokeLinejoin: 'round', 'aria-hidden': true } as const;
type P = SVGProps<SVGSVGElement>;

export const HomeIcon = (p: P) => (<svg {...base} {...p}><path d="M3 11l9-7 9 7" /><path d="M5 10v10h14V10" /></svg>);
export const GridIcon = (p: P) => (<svg {...base} {...p}><rect x="3" y="3" width="7" height="7" rx="1.5" /><rect x="14" y="3" width="7" height="7" rx="1.5" /><rect x="3" y="14" width="7" height="7" rx="1.5" /><rect x="14" y="14" width="7" height="7" rx="1.5" /></svg>);
export const ThermoIcon = (p: P) => (<svg {...base} {...p}><path d="M14 14.8V5a2 2 0 10-4 0v9.8a4 4 0 104 0z" /></svg>);
export const BulbIcon = (p: P) => (<svg {...base} {...p}><path d="M9 18h6" /><path d="M10 21h4" /><path d="M12 3a6 6 0 00-4 10.5c.8.8 1 1.5 1 2.5h6c0-1 .2-1.7 1-2.5A6 6 0 0012 3z" /></svg>);
export const BoltIcon = (p: P) => (<svg {...base} {...p}><path d="M13 2L4 14h7l-1 8 9-12h-7z" /></svg>);
export const DeviceIcon = (p: P) => (<svg {...base} {...p}><rect x="5" y="2" width="14" height="20" rx="2" /><path d="M12 18h.01" /></svg>);
export const GearIcon = (p: P) => (<svg {...base} {...p}><circle cx="12" cy="12" r="3" /><path d="M12 2v3M12 19v3M2 12h3M19 12h3M4.9 4.9l2.1 2.1M17 17l2.1 2.1M4.9 19.1L7 17M17 7l2.1-2.1" /></svg>);
export const PlusIcon = (p: P) => (<svg {...base} {...p}><path d="M12 5v14M5 12h14" /></svg>);
export const FlameIcon = (p: P) => (<svg {...base} width={14} height={14} strokeWidth={2} {...p}><path d="M12 2c2 4-3 6-1 10a4 4 0 01-4 4 4 4 0 0010 0c0-5-5-8-5-14z" /></svg>);
export const SnowIcon = (p: P) => (<svg {...base} width={14} height={14} strokeWidth={2} {...p}><path d="M12 2v20M4 6l16 12M20 6L4 18" /></svg>);
export const CloudOffIcon = (p: P) => (<svg {...base} {...p}><path d="M3 3l18 18" /><path d="M8 8a5 5 0 00-1 9h10" /><path d="M12 6a5 5 0 015 5h1a3 3 0 012 5" /></svg>);
