import { ApiError, retryAfterSeconds } from '../../shared/api/client';
import { decodeUploadConfirmation, decodeUploadTicket, type UploadTicket } from './contracts';
import { mediaWrite } from './media-ui';

export const imageTypes = 'image/jpeg,image/png,image/webp,image/gif';

export function requestUpload(root: string, file: File, filename: string, tags: string[], isPortrait = false) {
  return mediaWrite(root, '/assets/upload-url', 'POST', decodeUploadTicket, {
    filename, contentType: file.type, sizeBytes: file.size, tags, ...(isPortrait && { isPortrait }),
  });
}

export function confirmUpload(root: string, ticket: UploadTicket) {
  return mediaWrite(root, '/assets/' + ticket.assetId + '/confirm', 'POST',
    (value) => decodeUploadConfirmation(value, ticket.assetId));
}

export function transferMediaFile(ticket: UploadTicket, file: File, controller: AbortController, onProgress?: (value: number) => void) {
  const { signal } = controller;
  return new Promise<void>((resolve, reject) => {
    const request = new XMLHttpRequest();
    request.open('PUT', ticket.uploadUrl);
    request.withCredentials = false;
    Object.entries(ticket.requiredHeaders).forEach(([key, value]) => request.setRequestHeader(key, value));
    if (onProgress) request.upload.onprogress = (event) => {
      if (event.lengthComputable && event.total > 0) onProgress(Math.min(100, Math.floor(100 * event.loaded / event.total)));
    };
    const abort = () => request.abort();
    signal.addEventListener('abort', abort, { once: true });
    request.onloadend = () => signal.removeEventListener('abort', abort);
    request.onload = () => request.status >= 200 && request.status < 300 ? resolve() :
      reject(new ApiError('文件传输失败', request.status, retryAfterSeconds(request.getResponseHeader('Retry-After'))));
    request.onerror = () => reject(new Error('传输结果未确认'));
    request.onabort = () => reject(new Error('传输已中断'));
    request.send(file);
  });
}
