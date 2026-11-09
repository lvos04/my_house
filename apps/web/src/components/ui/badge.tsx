// shadcn/ui Badge in de React 18-variant (forwardRef). Peil kent geen losse kleurvarianten: elke variant
// is een vaste betekenis (domeinchip, neutraal label, handmatig). Gezondheid gebruikt StatusBadge
// (src/components/peil/StatusBadge.tsx), nooit deze Badge. Recept: .pill in src/peil/componenten.css.
import * as React from 'react';
import { cva, type VariantProps } from 'class-variance-authority';
import { cn } from '../../lib/utils';

export const badgeVariants = cva('pill', {
  variants: {
    variant: {
      neutraal: 'neutral',
      verbonden: 'ok',
      verwarmen: 'heat',
      koelen: 'cool',
      handmatig: 'override',
      info: 'info',
    },
  },
  defaultVariants: { variant: 'neutraal' },
});

export interface BadgeProps extends React.HTMLAttributes<HTMLSpanElement>, VariantProps<typeof badgeVariants> {}

export const Badge = React.forwardRef<HTMLSpanElement, BadgeProps>(({ className, variant, ...props }, ref) => (
  <span ref={ref} data-slot="badge" className={cn(badgeVariants({ variant }), className)} {...props} />
));
Badge.displayName = 'Badge';
