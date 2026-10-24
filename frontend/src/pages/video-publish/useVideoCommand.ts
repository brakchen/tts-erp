import { useEffect, useRef, useState } from 'react';
import { ApiError, requestJson, scheduleReadDelay } from '../../shared/api/client';
import { commandFailure, type CommandFailure } from '../../shared/api/command-errors';
import { parseIdentityJson } from '../../shared/api/identity-json';

const labels = { caption: '发布文案', filename: '视频文件', sizeBytes: '视频大小', deviceSerial: '设备序列号', prompt: '正文 Prompt', appPackage: 'App 包名' };
// Static presentation copy only; neither response.message nor an unknown code is a display string.
const businessMessages: Record<string, string> = {
  UPLOAD_SIZE_MISMATCH: '上传对象大小与原文件不一致，请选择原文件后重新上传',
  UPLOAD_NOT_FOUND: '未找到上传对象，请核对后明确重试上传',
  UPLOAD_MIME_MISSING: '上传对象缺少视频类型，请重新上传原 MP4',
  UPLOAD_MIME_MISMATCH: '上传对象类型与 MP4 声明不一致，请重新选择原文件',
  UPLOAD_ETAG_MISSING: '上传对象缺少 ETag，未入队；请稍后核对后明确重试确认',
  VIDEO_TOO_LARGE: '视频超过服务端上限，请选择较小的 MP4',
  INVALID_VIDEO_TYPE: '视频类型必须为 video/mp4 且扩展名为 .mp4',
  CAPTION_REQUIRED: '发布文案不能为空', CAPTION_TOO_LONG: '发布文案超过服务端上限',
  IDEMPOTENCY_PAYLOAD_MISMATCH: '原幂等键的请求内容不同，请先核对原任务，不要重复新建',
  TASK_VERSION_CONFLICT: '任务版本已变化，请查询当前事实后重新决定',
  ROW_VERSION_CONFLICT: '任务版本已变化，请查询当前事实后重新决定',
  TASK_ALREADY_SUBMITTED: '任务已提交给发布端，不能取消或修改参数',
  TASK_SUBMITTING: '任务正在提交，请查询当前事实后再决定',
  TASK_BUSY: '任务正在执行，请查询当前事实后再决定',
  CLEANUP_REQUIRED: '设备或本地暂存清理失败，请先核对并重试清理',
  UPLOAD_REPLACEMENT_REQUIRED: '原对象不可用，请核对服务端提供的重新上传操作',
  PROMPT_INVALID: '正文 Prompt 含不支持的字符', PROMPT_TOO_LONG: '正文 Prompt 超过服务端长度上限',
  DEVICE_SERIAL_INVALID: '设备序列号格式无效', DEVICE_NOT_CONFIGURED: '发布设备未配置',
  APP_PACKAGE_INVALID: 'App 包名格式无效', PUBLISH_WORKER_UNAVAILABLE: '发布 Worker 不可用，请稍后核对后手动重试',
  OBJECT_STORE_UNAVAILABLE: '对象存储不可用，原操作结果需核对后再决定',
};
/** One page-level single-flight/cooldown gate. It never schedules or replays a command. */
export function useVideoCommand(root: string) {
  const [pending, setPending] = useState(false);
  const [cooldown, setCooldown] = useState(false);
  const [needsCheck, setNeedsCheck] = useState(false);
  const [failure, setFailure] = useState<CommandFailure>();
  const busy = useRef(false); const deadline = useRef(0); const uncertain = useRef(false); const attemptPath = useRef('');
  const mounted = useRef(true); const cancelDelay = useRef<(() => void) | undefined>(undefined);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; cancelDelay.current?.(); }; }, []);
  function reject(error: unknown) {
    if (!mounted.current) return;
    const failed = commandFailure(error, labels);
    if (error instanceof ApiError && error.detail && typeof error.detail === 'object' && !Array.isArray(error.detail) && 'code' in error.detail) {
      const code = error.detail.code;
      if (typeof code === 'string' && Object.hasOwn(businessMessages, code)) failed.message = `${businessMessages[code]}（HTTP ${error.status}）`;
    }
    setFailure(failed);
    uncertain.current = failed.uncertain || (error instanceof ApiError && [404, 409].includes(error.status));
    setNeedsCheck(uncertain.current);
    hold(error);
  }
  function hold(error: unknown) {
    const retryAfter = error instanceof ApiError ? error.retryAfter ?? 0 : 0;
    if (retryAfter > 0) {
      deadline.current = Math.max(deadline.current, performance.now() + retryAfter * 1000);
      cancelDelay.current?.(); setCooldown(true);
      cancelDelay.current = scheduleReadDelay(() => { if (mounted.current) setCooldown(false); }, deadline.current - performance.now());
    }
  }
  async function send<T>(path: string, body: Record<string, unknown>, decode: (value: unknown) => T, method = 'POST'): Promise<T | undefined> {
    if (busy.current || performance.now() < deadline.current || uncertain.current) return;
    busy.current = true; attemptPath.current = path; setPending(true); setFailure(undefined);
    try {
      const result = await requestJson<unknown>(root, path, { method, body: JSON.stringify(body) }, parseIdentityJson);
      const data = decode(result.data);
      return mounted.current ? data : undefined;
    } catch (error: unknown) { reject(error); return undefined; }
    finally { busy.current = false; if (mounted.current) setPending(false); }
  }
  function binaryPending(value: boolean) { busy.current = value; setPending(value); }
  function checked(path: string) { if (path !== attemptPath.current) return; uncertain.current = false; setNeedsCheck(false); }
  function edited() { setFailure((old) => old && Object.keys(old.fieldErrors).length ? { ...old, fieldErrors: {} } : old); }
  return { send, reject, hold, binaryPending, checked, attemptPath: attemptPath.current, edited, failure, pending, cooldown, needsCheck, blocked: pending || cooldown || needsCheck };
}
export type VideoCommand = ReturnType<typeof useVideoCommand>;
