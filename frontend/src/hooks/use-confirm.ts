import { useState } from 'react'

interface ConfirmOptions {
  title: string
  description: string
  confirmText?: string
  cancelText?: string
  destructive?: boolean
}

interface ConfirmState extends ConfirmOptions {
  open: boolean
  resolve?: (confirmed: boolean) => void
}

export function useConfirm() {
  const [state, setState] = useState<ConfirmState>({
    open: false,
    title: '',
    description: '',
    confirmText: 'Confirm',
    cancelText: 'Cancel',
    destructive: false
  })

  const confirm = (options: ConfirmOptions): Promise<boolean> => {
    return new Promise((resolve) => {
      setState({
        ...options,
        open: true,
        resolve
      })
    })
  }

  const handleConfirm = () => {
    state.resolve?.(true)
    setState(prev => ({ ...prev, open: false, resolve: undefined }))
  }

  const handleCancel = () => {
    state.resolve?.(false)
    setState(prev => ({ ...prev, open: false, resolve: undefined }))
  }

  const handleOpenChange = (open: boolean) => {
    if (!open) {
      handleCancel()
    }
  }

  return {
    confirm,
    confirmProps: {
      open: state.open,
      onOpenChange: handleOpenChange,
      title: state.title,
      description: state.description,
      confirmText: state.confirmText,
      cancelText: state.cancelText,
      onConfirm: handleConfirm,
      destructive: state.destructive
    }
  }
}