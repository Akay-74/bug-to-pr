interface StatusChipProps {
  status: 'verified' | 'failed' | 'running' | 'pending'
  size?: 'sm' | 'md'
}

const statusConfig: Record<string, { icon: string; label: string; classes: string; animate?: boolean }> = {
  verified: {
    icon: 'verified',
    label: 'VERIFIED',
    classes: 'bg-surface-container-low border-surface-variant text-primary-container',
  },
  failed: {
    icon: 'error',
    label: 'FAILED',
    classes: 'bg-surface-container-low border-surface-variant text-error',
  },
  running: {
    icon: 'progress_activity',
    label: 'RUNNING',
    classes: 'bg-primary-container/20 border-primary text-primary',
    animate: true,
  },
  pending: {
    icon: 'schedule',
    label: 'PENDING',
    classes: 'bg-surface-container-low border-surface-variant text-on-surface-variant',
  },
}

export function StatusChip({ status, size = 'sm' }: StatusChipProps) {
  const config = statusConfig[status]
  return (
    <div className={`inline-flex items-center gap-1 px-2 py-0.5 rounded border text-[10px] font-bold tracking-[0.05em] ${config.classes}`}>
      <span className={`material-symbols-outlined text-[12px] ${config.animate ? 'animate-spin' : ''}`}>
        {config.icon}
      </span>
      {config.label}
    </div>
  )
}
