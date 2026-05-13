import * as React from 'react';
import { cva, type VariantProps } from 'class-variance-authority';
import { cn } from '../../lib/utils';

const buttonVariants = cva(
  'inline-flex items-center justify-center rounded-full border text-sm font-semibold transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-offset-2 disabled:pointer-events-none disabled:opacity-50',
  {
    variants: {
      variant: {
        default: 'border-transparent bg-ink px-4 py-2 text-fog hover:bg-tide',
        subtle: 'border-sand/70 bg-white/70 px-4 py-2 text-ink hover:border-clay hover:text-clay',
        ghost: 'border-transparent px-3 py-2 text-ink/75 hover:bg-white/60 hover:text-ink',
      },
      size: {
        default: 'h-10',
        sm: 'h-8 px-3 text-xs',
      },
    },
    defaultVariants: {
      variant: 'default',
      size: 'default',
    },
  },
);

export interface ButtonProps
  extends React.ButtonHTMLAttributes<HTMLButtonElement>,
    VariantProps<typeof buttonVariants> {}

export const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, size, variant, ...props }, ref) => (
    <button ref={ref} className={cn(buttonVariants({ size, variant }), className)} {...props} />
  ),
);

Button.displayName = 'Button';
