// Iconen van de web-app: lucide (Peil, ONTWERPTAAL §7 en docs/ontwerp/PEIL_MIGRATIE.md §5.1). De oude
// exportnamen blijven, zodat de aanroepen niet veranderen. Eén icoon per betekenis; iconen naast tekst zijn
// aria-hidden (lucide zet dat zelf zolang er geen aria-label of title is). 20 px in knoppen en titels,
// 18 px in badges, 24 px in de ondernavigatie (CSS).
import type { SVGProps } from 'react';
import {
  ArrowLeft,
  Ban,
  Check,
  ChevronRight,
  CircleCheck,
  CircleHelp,
  CirclePause,
  ClockAlert,
  Copy,
  Flame,
  Hand,
  Hourglass,
  House,
  Info,
  LayoutGrid,
  LifeBuoy,
  Lightbulb,
  LightbulbOff,
  Link2,
  Link2Off,
  LoaderCircle,
  Lock,
  LogOut,
  Menu,
  OctagonX,
  Plug,
  Plus,
  RotateCcw,
  Settings,
  ShowerHead,
  Snowflake,
  Thermometer,
  TrendingUp,
  TriangleAlert,
  Upload,
  User,
  WifiOff,
  Zap,
  type LucideIcon,
} from 'lucide-react';

type P = Omit<SVGProps<SVGSVGElement>, 'ref'>;

function icon(I: LucideIcon, size: number, name: string) {
  const C = (p: P) => <I size={size} strokeWidth={2} {...p} />;
  C.displayName = name;
  return C;
}

// Navigatie en merk
export const HomeIcon = icon(House, 20, 'HomeIcon');
export const GridIcon = icon(LayoutGrid, 20, 'GridIcon');
export const ThermoIcon = icon(Thermometer, 20, 'ThermoIcon');
export const BulbIcon = icon(Lightbulb, 20, 'BulbIcon');
export const BulbOffIcon = icon(LightbulbOff, 20, 'BulbOffIcon');
export const BoltIcon = icon(Zap, 20, 'BoltIcon');
export const TrendIcon = icon(TrendingUp, 20, 'TrendIcon');
/** Apparaten: een stekker (het oude telefoonsymbool leek op "Telefoons"). */
export const DeviceIcon = icon(Plug, 20, 'DeviceIcon');
export const GearIcon = icon(Settings, 20, 'GearIcon');
export const UserIcon = icon(User, 20, 'UserIcon');
/** Help: reddingsboei; `circle-help` is gereserveerd voor de status Onbekend. */
export const HelpIcon = icon(LifeBuoy, 20, 'HelpIcon');
export const MenuIcon = icon(Menu, 20, 'MenuIcon');
export const BackIcon = icon(ArrowLeft, 20, 'BackIcon');
export const LogOutIcon = icon(LogOut, 20, 'LogOutIcon');
export const ChevronIcon = icon(ChevronRight, 20, 'ChevronIcon');

// Handelingen
export const PlusIcon = icon(Plus, 20, 'PlusIcon');
export const CheckIcon = icon(Check, 20, 'CheckIcon');
export const CopyIcon = icon(Copy, 20, 'CopyIcon');
export const UploadIcon = icon(Upload, 20, 'UploadIcon');
export const RetryIcon = icon(RotateCcw, 20, 'RetryIcon');

// Domein (badges: 18 px)
export const FlameIcon = icon(Flame, 18, 'FlameIcon');
export const SnowIcon = icon(Snowflake, 18, 'SnowIcon');
export const BoilerIcon = icon(ShowerHead, 18, 'BoilerIcon');
export const HandIcon = icon(Hand, 18, 'HandIcon');
export const LockIcon = icon(Lock, 18, 'LockIcon');
export const PolicyIcon = icon(Ban, 18, 'PolicyIcon');
export const LinkedIcon = icon(Link2, 18, 'LinkedIcon');
export const UnlinkedIcon = icon(Link2Off, 18, 'UnlinkedIcon');
export const BusyIcon = icon(LoaderCircle, 18, 'BusyIcon');
export const NotYetIcon = icon(Hourglass, 18, 'NotYetIcon');

// Verbinding en status (ONTWERPTAAL §4.3: vaste koppeling)
export const CloudOffIcon = icon(WifiOff, 20, 'CloudOffIcon');
export const HealthyIcon = icon(CircleCheck, 18, 'HealthyIcon');
export const DegradedIcon = icon(TriangleAlert, 18, 'DegradedIcon');
export const DownIcon = icon(OctagonX, 18, 'DownIcon');
export const UnknownIcon = icon(CircleHelp, 18, 'UnknownIcon');
export const StaleIcon = icon(ClockAlert, 18, 'StaleIcon');
export const PausedIcon = icon(CirclePause, 18, 'PausedIcon');
export const InfoIcon = icon(Info, 18, 'InfoIcon');
