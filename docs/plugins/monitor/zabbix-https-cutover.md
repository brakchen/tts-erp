# Monitor 公网 HTTPS 接入与切换

## 人工退役旧接口

在 HP Desk 的 ERP 仓库获取已合并的版本。退役应用提交为 `b263acfa1cdd1b79c63dc6db561826b4d22dec0f`，该提交已完成隔离基线验证。本次退役无需数据库迁移。

```bash
cd ~/tts-erp
git fetch origin master
bash scripts/prod_deploy.sh b263acfa1cdd1b79c63dc6db561826b4d22dec0f
```

生产脚本交互式要求输入目标主机 IP `85.189.59.112`。发布成功后，用以下命令发布 HP Desk 并重启同步 worker。当前 HP Desk release 目录中的 `logs` 是指向主仓日志目录的符号链接。下面的 Git 排除设置只作用于此部署进程，脚本仍会因其他未跟踪或已修改文件而停止。

```bash
(
    set -e
    release="$HOME/tts-erp-releases/erp-master-01a16e3a"
    test -L "$release/logs"
    test "$(readlink -f "$release/logs")" = "$(readlink -f "$HOME/tts-erp/logs")"
    ignore=$(mktemp)
    trap 'rm -f -- "$ignore"' EXIT
    printf '/logs\n' > "$ignore"
    export GIT_CONFIG_COUNT=1
    export GIT_CONFIG_KEY_0=core.excludesFile
    export GIT_CONFIG_VALUE_0="$ignore"
    test -z "$(git -C "$release" status --porcelain)"
    bash scripts/test_deploy.sh b263acfa1cdd1b79c63dc6db561826b4d22dec0f
    systemctl --user restart tts-erp-sync.service
    systemctl --user is-active --quiet tts-erp-sync.service
)
```

分别检查公网 `https://chen.daqiangplatform.com/tts/endpoints` 和 HP Desk `http://127.0.0.1:9877/endpoints`。两个清单都应保留 `/v2/plugin/shops/discover`，并移除 `/v2/plugin/monitor/health`。在 HP Desk 验证 `tts-erp-sync.service` 从新 release 启动。

## Zabbix 上报入口

ERP 完成退役后，在生产机的现有 TLS 虚拟主机配置精确路径 `https://chen.daqiangplatform.com/monitor-zabbix/api_jsonrpc.php`。入口只接受当前 Monitor 的 `history.push` 批次，Zabbix 继续校验 Token 和店铺授权。Nginx 使用已有的 FRP 入口 `127.0.0.1:6002` 转发。通过 Debian 软件包 `libnginx-mod-http-js` 提供 njs 模块，并确认模块已加载。

将下段加入 `http` 上下文。

```nginx
js_import monitor_zabbix from /etc/nginx/njs/monitor-zabbix.js;
limit_req_zone $binary_remote_addr zone=monitor_zabbix:1m rate=10r/s;
```

将下段加入 `chen.daqiangplatform.com` 的 443 `server` 上下文。

```nginx
location = /monitor-zabbix/api_jsonrpc.php {
    access_log off;
    client_max_body_size 16k;
    client_body_buffer_size 16k;
    limit_req zone=monitor_zabbix burst=30 nodelay;
    limit_req_status 429;
    subrequest_output_buffer_size 32k;
    js_content monitor_zabbix.push;
}

location = /_internal/monitor-zabbix {
    internal;
    proxy_pass http://127.0.0.1:6002/api_jsonrpc.php;
    proxy_pass_request_headers off;
    proxy_set_header Host 127.0.0.1;
    proxy_set_header Authorization $http_authorization;
    proxy_set_header Content-Type application/json-rpc;
    proxy_connect_timeout 1s;
    proxy_send_timeout 2s;
    proxy_read_timeout 2s;
}
```

将以下内容保存为 `/etc/nginx/njs/monitor-zabbix.js`。

```javascript
function push(r) {
    if (r.method !== "POST") {
        r.headersOut.Allow = "POST";
        r.return(405);
        return;
    }
    if (!/^Bearer [A-Za-z0-9._~-]+$/.test(r.headersIn.Authorization || "")) {
        r.return(401);
        return;
    }
    if (r.variables.args) {
        r.return(400);
        return;
    }

    let request;
    try {
        request = JSON.parse(r.requestText);
    } catch (_) {
        r.return(400);
        return;
    }
    if (!request || request.jsonrpc !== "2.0" || request.method !== "history.push" ||
        !Number.isSafeInteger(request.id) || !Array.isArray(request.params) ||
        request.params.length === 0 || request.params.length > 8 ||
        !request.params.every(validItem)) {
        r.return(400);
        return;
    }

    const body = JSON.stringify({
        jsonrpc: "2.0",
        method: "history.push",
        id: request.id,
        params: request.params
    });
    r.subrequest("/_internal/monitor-zabbix", { method: "POST", body }, (response) => {
        r.headersOut["Content-Type"] = "application/json";
        r.return(response.status, response.responseText);
    });
}

function validItem(item) {
    return item && Object.keys(item).length === 3 &&
        typeof item.host === "string" && /^tts-monitor-[0-9]+$/.test(item.host) &&
        typeof item.key === "string" &&
        (typeof item.value === "string" || Number.isFinite(item.value));
}

export default { push };
```

由网关维护者执行 `nginx -t`，通过后 reload。验证无 Token 返回 401、GET 返回 405、使用 Token 的非 `history.push` 请求返回 400，内部路径无法直接访问。用受限 Token 上报一个测试店铺，确认 Zabbix 返回逐项成功，再按 [Zabbix 接入指南](zabbix-monitor.md) 配置主机与飞书告警。回退时移除这两处配置引用，执行 `nginx -t` 后 reload。

2026-11-05 在隔离容器中用 Debian 13、Nginx 1.26.3 和 njs 0.8.9 验证了配置语法、TLS、路由与大小限制、仅转发预期请求及保留上游错误状态。另从当时生产机的回环 FRP 端口只读确认上游 Zabbix 版本为 7.4.15。上线仍需用正式域名和受限 Token 验证。

参考 [NGINX njs HTTP 模块](https://nginx.org/en/docs/http/ngx_http_js_module.html)、[njs 请求对象](https://nginx.org/en/docs/njs/reference.html) 和 [Debian njs 模块包](https://packages.debian.org/trixie/libnginx-mod-http-js)。
