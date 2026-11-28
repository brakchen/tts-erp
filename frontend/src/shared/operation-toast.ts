import { useEffect, useMemo, useRef } from 'react';
import { toast } from '@heroui/react';

const SUCCESS_TIMEOUT = 4000;
const FAILURE_TIMEOUT = 6000;

export interface OperationToast {
  /** 操作成功：短时展示后自动消失。 */
  success(title: string, description?: string): void;
  /** 操作失败：保留更久；结果不确定（可能已生效）时不自动消失，由用户核对。 */
  failure(title: string, description?: string, options?: { uncertain?: boolean }): void;
  /** 非成败类提示（例如流程阻断、草稿冲突）。 */
  warning(title: string, description?: string): void;
  /** 请求等待状态：带加载指示、不自动消失；返回关闭句柄，请求结束后必须调用。 */
  pending(title: string, description?: string): () => void;
  dismiss(): void;
}

/**
 * 写操作反馈的统一入口（AppShell 已挂 Toast.Provider）。
 * 多次反馈各自独立展示并自动过期，组件卸载时关闭仍未消失的反馈。
 * 一次性操作结果用 success/failure/warning；请求等待状态用 pending（登录页已采用）。
 * 空结果、服务端冷却倒计时、结果核对流程等持续性页面状态仍保留内联。
 */
export function useOperationToast(): OperationToast {
  const active = useRef<string | undefined>(undefined);
  useEffect(() => () => { if (active.current) toast.close(active.current); }, []);
  return useMemo<OperationToast>(() => {
    const show = (id: string) => { active.current = id; return id; };
    return {
      success(title, description) {
        show(toast.success(title, { description, timeout: SUCCESS_TIMEOUT }));
      },
      failure(title, description, options) {
        show(toast.danger(title, { description, timeout: options?.uncertain ? 0 : FAILURE_TIMEOUT }));
      },
      warning(title, description) {
        show(toast.warning(title, { description, timeout: FAILURE_TIMEOUT }));
      },
      pending(title, description) {
        const id = show(toast(title, { description, isLoading: true, timeout: 0 }));
        return () => {
          if (active.current === id) active.current = undefined;
          toast.close(id);
        };
      },
      dismiss() {
        if (active.current) toast.close(active.current);
        active.current = undefined;
      },
    };
  }, []);
}
