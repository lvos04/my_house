// shadcn/ui Button in de React 18-variant (forwardRef + displayName), met de Peil-varianten uit
// COMPONENTEN.md §1. Het uiterlijk staat één keer als recept bij de haakklassen (.btn, .primary, …) in
// src/peil/componenten.css (@layer components, Tailwind-utilities via @apply). Zo zien <Button> en de
// bestaande <button className="btn …"> er precies hetzelfde uit en blijven de haakklassen voor tests en de
// aliaslaag bestaan (docs/adr/ADR-0009-peil-fase-2-tailwind-shadcn-react18.md).
import * as React from 'react';
import { Slot } from '@radix-ui/react-slot';
import { cva, type VariantProps } from 'class-variance-authority';
import { cn } from '../../lib/utils';

export const buttonVariants = cva('btn', {
  variants: {
    variant: {
      secundair: '',
      primair: 'primary',
      stil: 'ghost',
      gevaar: 'danger',
      link: 'link-btn',
    },
    size: {
      standaard: '',
      klein: 'small',
      icoon: 'btn-icon',
    },
  },
  defaultVariants: { variant: 'secundair', size: 'standaard' },
});

export interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement>, VariantProps<typeof buttonVariants> {
  /** Render het kind (bijv. een router-<Link>) met de knopstijl in plaats van een <button>. */
  asChild?: boolean;
}

export const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant, size, asChild = false, type, ...props }, ref) => {
    const Comp = asChild ? Slot : 'button';
    return (
      <Comp
        ref={ref}
        data-slot="button"
        className={cn(buttonVariants({ variant, size }), className)}
        {...(asChild ? {} : { type: type ?? 'button' })}
        {...props}
      />
    );
  },
);
Button.displayName = 'Button';
