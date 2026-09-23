# Task #634 完成报告 — P2.5+16 web_fetch.py + cache.py

## 落盘
- `prisir_work/cache.py` 6482 bytes mtime 21:13
- `prisir_work/web_fetch.py` 16822 bytes mtime 21:14
- `tests/test_web_fetch.py` 11250 bytes mtime 21:13

## 测试
```
============================== 8 passed in 0.41s ==============================
tests/test_web_fetch.py::test_cache_roundtrip PASSED                     [ 12%]
tests/test_web_fetch.py::test_cache_concurrent_put PASSED                [ 25%]
tests/test_web_fetch.py::test_fetch_uses_cache PASSED                    [ 37%]
tests/test_web_fetch.py::test_fetch_fallback_to_empty PASSED             [ 50%]
tests/test_web_fetch.py::test_fetch_picks_fastest PASSED                 [ 62%]
tests/test_web_fetch.py::test_url_normalize PASSED                       [ 75%]
tests/test_web_fetch.py::test_a11y_local_only PASSED                     [ 87%]
tests/test_web_fetch.py::test_disk_cache_path PASSED                     [100%]
```

## 设计要点
- cache.py:`cache_dir()` 跨平台(Windows=%LOCALAPPDATA%/PrisirAI/cache/web),`cache_get` 不删过期(防并发竞态),`cache_put` 用 tempfile.mkstemp + os.replace 原子写,异常全吞
- web_fetch.py:`register_fetcher` / `fetch(url, options, timeout)` 公开 API;默认装 http_urllib(UA + gzip + 2xx)/ a11y_provider(file:// only)/ browser_use_cli_provider(http/https only);内存 LRU(sha1(url),max 32)兜底
- **并发竞速**:as_completed(timeout=…) 拿第一个成功 result 就 break + pool.shutdown(wait=False),慢 fetcher 不拖时间
- URL 规范化:去 fragment 保留 query,缓存命中更稳
- 所有路径异常都吞,绝不 raise

## 偏离规范
- 默认 max_workers=4(原 spec 没明确,选 4 平衡并发与系统负载)