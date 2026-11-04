import { useEffect, useRef, useState } from 'react';
import { Alert, Button, Input, Label, TextArea, TextField } from '@heroui/react';
import type { PageProps } from '../../shared/contracts';
import { ApiError, requestJson, retryAfterSeconds } from '../../shared/api/client';
import { parseIdentityJson } from '../../shared/api/identity-json';
import {
  assetKinds,
  decodeAsset,
  decodeAssetList,
  decodeUploadConfirmation,
  decodeUploadTicket,
  displayBytes,
  type Asset,
} from './contracts';

const api = '/v2/media';
const pageSize = 100;

function readFailure(error: unknown): string {
  if (!(error instanceof ApiError)) return '读取未完成，请手动重试';
  if (error.status === 401) return '会话已失效（HTTP 401），请重新登录';
  if (error.status === 403) return '无权读取媒体资产（HTTP 403）';
  if (error.status === 404) return '当前记录不存在（HTTP 404）';
  if (error.status === 429) return '请求过于频繁（HTTP 429），请等待后手动重试';
  return `读取失败（HTTP ${error.status}），请手动重试`;
}

function writeFailure(error: unknown): string {
  if (!(error instanceof ApiError)) return '操作未完成，请手动重试';
  if (error.status === 401) return '会话已失效（HTTP 401），请重新登录';
  if (error.status === 403) return '无权执行该操作（HTTP 403）';
  if (error.status === 503) return '对象存储暂不可用（HTTP 503），请稍后重试';
  if (error.status === 409) return '与服务端当前状态冲突（HTTP 409），请刷新后核对';
  if (error.status === 429) return '请求过于频繁（HTTP 429），请等待后手动重试';
  return `操作失败（HTTP ${error.status}）`;
}

function AssetPreview({ asset }: { asset: Asset }) {
  if (!asset.url) return <p>该资产暂无预览地址。</p>;
  if (asset.kind === 'video') {
    return (
      <video
        src={asset.url}
        aria-label={`视频预览 ${asset.filename}`}
        controls
        preload="metadata"
        className="max-h-72 max-w-full"
      />
    );
  }
  return (
    <img
      src={asset.url}
      alt={`图片预览 ${asset.filename}`}
      className="max-h-72 max-w-full object-contain"
    />
  );
}

export default function MediaAssets({ bootstrap }: PageProps) {
  const root = bootstrap.rootPath;
  const canWrite = bootstrap.roleCapabilities?.readwrite === true;
  const [revision, setRevision] = useState(0);
  const [detailRevision, setDetailRevision] = useState(0);
  const [offset, setOffset] = useState(0);
  const [kind, setKind] = useState('');
  const [keyword, setKeyword] = useState('');
  const [assets, setAssets] = useState<Asset[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [listError, setListError] = useState('');
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [loadedDetail, setDetail] = useState<Asset>();
  const detail = loadedDetail?.assetId === selectedId ? loadedDetail : undefined;
  const [detailError, setDetailError] = useState('');
  const [editName, setEditName] = useState('');
  const [editDescription, setEditDescription] = useState('');
  const [editTags, setEditTags] = useState('');
  const [notice, setNotice] = useState('');
  const [file, setFile] = useState<File>();
  const [uploading, setUploading] = useState(false);
  const [progress, setProgress] = useState(0);
  const xhr = useRef<XMLHttpRequest | undefined>(undefined);

  useEffect(() => () => xhr.current?.abort(), []);

  const params = new URLSearchParams();
  if (kind) params.set('kind', kind);
  if (keyword.trim()) params.set('keyword', keyword.trim());
  params.set('limit', String(pageSize));
  if (offset) params.set('offset', String(offset));
  const listPath = `${api}/assets?${params.toString()}`;

  useEffect(() => {
    const controller = new AbortController();
    let active = true;
    setLoading(true);
    setListError('');
    void requestJson<unknown>(root, listPath, { signal: controller.signal }, parseIdentityJson)
      .then((result) => {
        if (!active) return;
        const page = decodeAssetList(result.data);
        setAssets(page.items);
        setTotal(Number(page.total));
      })
      .catch((error: unknown) => {
        if (!active) return;
        setAssets([]);
        setTotal(0);
        setListError(readFailure(error));
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
      controller.abort();
    };
  }, [root, listPath, revision]);

  useEffect(() => {
    if (!selectedId) {
      setDetail(undefined);
      setDetailError('');
      return;
    }
    const controller = new AbortController();
    let active = true;
    setDetailError('');
    void requestJson<unknown>(root, `${api}/assets/${selectedId}`, { signal: controller.signal }, parseIdentityJson)
      .then((result) => {
        if (!active) return;
        const asset = decodeAsset(result.data);
        setDetail(asset);
        setEditName(asset.filename);
        setEditDescription(asset.description ?? '');
        setEditTags(asset.tags.join('、'));
      })
      .catch((error: unknown) => {
        if (!active) return;
        setDetail(undefined);
        setDetailError(readFailure(error));
      });
    return () => {
      active = false;
      controller.abort();
    };
  }, [root, selectedId, detailRevision]);

  async function upload(next?: File) {
    if (!next || !canWrite || uploading) return;
    setFile(next);
    setNotice('');
    setUploading(true);
    setProgress(0);
    try {
      const ticket = await requestJson<unknown>(
        root,
        `${api}/assets/upload-url`,
        {
          method: 'POST',
          body: JSON.stringify({
            filename: next.name,
            contentType: next.type,
            sizeBytes: next.size,
          }),
        },
        parseIdentityJson,
      ).then((result) => decodeUploadTicket(result.data));

      const request = new XMLHttpRequest();
      xhr.current = request;
      request.open('PUT', ticket.uploadUrl);
      request.withCredentials = false;
      for (const [name, value] of Object.entries(ticket.requiredHeaders)) {
        request.setRequestHeader(name, value);
      }
      request.upload.onprogress = (event) => {
        if (event.lengthComputable) setProgress(Math.round((event.loaded / event.total) * 100));
      };
      await new Promise<void>((resolve, reject) => {
        request.onload = () =>
          request.status >= 200 && request.status < 300
            ? resolve()
            : reject(
                new ApiError(
                  '文件上传未完成',
                  request.status,
                  retryAfterSeconds(request.getResponseHeader('Retry-After')),
                ),
              );
        request.onerror = () => reject(new Error('文件上传中断'));
        request.onabort = () => reject(new Error('上传已停止'));
        request.send(next);
      });

      const sizeBytes = await requestJson<unknown>(
        root,
        `${api}/assets/${ticket.assetId}/confirm`,
        { method: 'POST' },
        parseIdentityJson,
      ).then((result) => decodeUploadConfirmation(result.data, ticket.assetId));

      setNotice(`已入库：${next.name}（${displayBytes(sizeBytes)}）。`);
      setFile(undefined);
      setRevision((value) => value + 1);
    } catch (error: unknown) {
      setNotice(`上传未完成：${writeFailure(error)}`);
    } finally {
      setUploading(false);
      xhr.current = undefined;
    }
  }

  async function saveDetail() {
    if (!detail || !canWrite) return;
    setNotice('');
    try {
      const updated = await requestJson<unknown>(
        root,
        `${api}/assets/${detail.assetId}`,
        {
          method: 'PATCH',
          body: JSON.stringify({
            filename: editName,
            description: editDescription,
            tags: editTags
              .split(/[、,，]/)
              .map((tag) => tag.trim())
              .filter((tag) => tag.length > 0),
          }),
        },
        parseIdentityJson,
      ).then((result) => decodeAsset(result.data));
      setDetail(updated);
      setNotice('资产信息已保存。');
      setRevision((value) => value + 1);
    } catch (error: unknown) {
      setNotice(`保存未完成：${writeFailure(error)}`);
    }
  }

  async function removeAsset() {
    if (!detail || !canWrite) return;
    setNotice('');
    try {
      await requestJson<unknown>(root, `${api}/assets/${detail.assetId}`, {
        method: 'DELETE',
      });
      setNotice('资产已删除。');
      setSelectedId(null);
      setDetail(undefined);
      if (assets.length === 1 && assets[0].assetId === detail.assetId) {
        setOffset(Math.max(0, offset - pageSize));
      }
      setRevision((value) => value + 1);
    } catch (error: unknown) {
      setNotice(`删除未完成：${writeFailure(error)}`);
    }
  }

  return (
    <div className="erp-page">
      <div className="erp-panel space-y-3">
        <h2 className="text-xl font-semibold">上传资产</h2>
        <p>图片上限 20 MiB，视频上限 200 MiB；文件直传对象存储，服务端只保存元数据。</p>
        <label className="block">
          选择文件
          <input
            type="file"
            accept="image/jpeg,image/png,image/webp,image/gif,video/mp4,video/webm,video/quicktime"
            disabled={!canWrite || uploading}
            onChange={(event) => {
              const chosen = event.currentTarget.files?.[0];
              event.currentTarget.value = '';
              void upload(chosen);
            }}
          />
        </label>
        {file && <p>{file.name} · {displayBytes(String(file.size))}</p>}
        {uploading && (
          <progress aria-label="上传进度" max={100} value={progress}>
            {progress}%
          </progress>
        )}
        {!canWrite && <p>上传需要服务端提供的 readwrite 能力提示；接口会再次验证权限。</p>}
      </div>

      <div className="erp-panel space-y-3">
        <h2 className="text-xl font-semibold">资产库</h2>
        <div className="erp-filter-row">
          <label className="erp-field">
            类型
            <select
              className="erp-select"
              aria-label="资产类型"
              value={kind}
              onChange={(event) => {
                setKind(event.target.value);
                setOffset(0);
              }}
            >
              {assetKinds.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
          </label>
          <label className="erp-field">
            关键字
            <Input
              value={keyword}
              aria-label="资产关键字"
              maxLength={120}
              onChange={(event) => {
                setKeyword(event.target.value);
                setOffset(0);
              }}
            />
          </label>
        </div>
        {loading && <p role="status">加载资产中…</p>}
        {listError && (
          <Alert status="danger">
            <Alert.Content>
              <Alert.Title>资产列表不可用</Alert.Title>
              <Alert.Description>{listError}</Alert.Description>
              <Button variant="secondary" onPress={() => setRevision((value) => value + 1)}>重试列表</Button>
            </Alert.Content>
          </Alert>
        )}
        {!loading && !listError && <p>共 {total} 项资产。</p>}
        <ul className="space-y-2">
          {assets.map((asset) => (
            <li key={asset.assetId} className="flex flex-wrap items-center gap-3">
              <Button
                variant="secondary"
                onPress={() => setSelectedId(asset.assetId)}
              >
                查看 {asset.filename}
              </Button>
              <span>
                {asset.kind === 'video' ? '视频' : '图片'} ·{' '}
                {displayBytes(asset.sizeBytes)} · {asset.status}
              </span>
              {asset.tags.length > 0 && <span>标签：{asset.tags.join('、')}</span>}
            </li>
          ))}
        </ul>
        {(offset > 0 || total > pageSize) && (
          <nav aria-label="资产分页" className="flex flex-wrap gap-2">
            <Button variant="secondary" isDisabled={loading || offset === 0} onPress={() => setOffset(offset - pageSize)}>上一页</Button>
            <Button variant="secondary" isDisabled={loading || offset + pageSize >= total} onPress={() => setOffset(offset + pageSize)}>下一页</Button>
          </nav>
        )}
        {!loading && !listError && assets.length === 0 && (
          <p>当前没有符合条件的资产。</p>
        )}
      </div>

      {selectedId && (
        <div className="erp-panel space-y-3">
          <h2 className="text-xl font-semibold">资产详情</h2>
          {!detail && !detailError && <p role="status">加载资产详情中…</p>}
          {detailError && (
            <Alert status="danger">
              <Alert.Content>
                <Alert.Title>详情不可用</Alert.Title>
                <Alert.Description>{detailError}</Alert.Description>
                <Button variant="secondary" onPress={() => setDetailRevision((value) => value + 1)}>重试详情</Button>
              </Alert.Content>
            </Alert>
          )}
          {detail && (
            <>
              <AssetPreview asset={detail} />
              <p>
                {detail.filename} · {detail.contentType} ·{' '}
                {displayBytes(detail.sizeBytes)} · 状态 {detail.status}
              </p>
              {detail.failureReason && (
                <p role="alert">失败原因：{detail.failureReason}</p>
              )}
              <TextField
                value={editName}
                isDisabled={!canWrite}
                onChange={setEditName}
              >
                <Label>文件名</Label>
                <Input />
              </TextField>
              <TextField
                value={editDescription}
                isDisabled={!canWrite}
                onChange={setEditDescription}
              >
                <Label>说明</Label>
                <TextArea rows={3} autoComplete="off" />
              </TextField>
              <TextField
                value={editTags}
                isDisabled={!canWrite}
                onChange={setEditTags}
              >
                <Label>标签（顿号或逗号分隔）</Label>
                <Input />
              </TextField>
              <div className="flex flex-wrap gap-2">
                <Button isDisabled={!canWrite} onPress={() => void saveDetail()}>
                  保存
                </Button>
                <Button
                  variant="danger"
                  isDisabled={!canWrite}
                  onPress={() => void removeAsset()}
                >
                  删除资产
                </Button>
              </div>
            </>
          )}
        </div>
      )}

      {notice && <p role="status">{notice}</p>}
    </div>
  );
}
