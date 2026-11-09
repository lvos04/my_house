// shadcn/ui Card in de React 18-variant (forwardRef). Recept: .card in src/peil/componenten.css
// (bg-card, 1 px rand, radius 8, p-4, gap-3; geen schaduw). `asChild` laat het eigen element staan
// (section/article met aria-labelledby, zoals de bestaande kaarten).
import * as React from 'react';
import { Slot } from '@radix-ui/react-slot';
import { cn } from '../../lib/utils';

export interface CardProps extends React.HTMLAttributes<HTMLDivElement> {
  asChild?: boolean;
}

export const Card = React.forwardRef<HTMLDivElement, CardProps>(({ className, asChild = false, ...props }, ref) => {
  const Comp = asChild ? Slot : 'div';
  return <Comp ref={ref} data-slot="card" className={cn('card', className)} {...props} />;
});
Card.displayName = 'Card';

export const CardTitle = React.forwardRef<HTMLHeadingElement, React.HTMLAttributes<HTMLHeadingElement>>(({ className, ...props }, ref) => (
  <h3 ref={ref} data-slot="card-title" className={cn('text-lg font-semibold', className)} {...props} />
));
CardTitle.displayName = 'CardTitle';
