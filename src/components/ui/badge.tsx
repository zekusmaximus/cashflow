import type { HTMLAttributes } from 'react';
import { cn } from '../../lib/utils';

const toneClasses = {
  neutral: 'bg-sand/25 text-ink',
  success: 'bg-moss/15 text-moss',
  warning: 'bg-clay/15 text-clay',
  danger: 'bg-ember/15 text-ember',
  info: 'bg-tide/15 text-tide',
} as const;

type BadgeTone = keyof typeof toneClasses;

interface BadgeProps extends HTMLAttributes<HTMLSpanElement> {
  tone?: BadgeTone;
}

export function Badge({ className, tone = 'neutral', ...props }: BadgeProps) {
  return (
    <span
      className={cn(
        'inline-flex items-center rounded-full px-3 py-1 text-xs font-medium uppercase tracking-[0.18em]',
        toneClasses[tone],
        className,
      )}
      {...props}
    />
  );
}
