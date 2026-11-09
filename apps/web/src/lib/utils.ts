import { clsx, type ClassValue } from 'clsx';
import { twMerge } from 'tailwind-merge';

/** Klassen samenvoegen zoals shadcn/ui: later wint (tailwind-merge), falsy waarden vallen weg (clsx). */
export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs));
}
