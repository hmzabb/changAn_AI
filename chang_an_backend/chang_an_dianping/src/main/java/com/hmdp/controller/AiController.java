package com.hmdp.controller;

import cn.hutool.http.HttpRequest;
import cn.hutool.http.HttpResponse;
import cn.hutool.json.JSONUtil;
import com.hmdp.dto.Result;
import lombok.extern.slf4j.Slf4j;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

/**
 * AI 辅助接口转发层
 *
 * 设计要点（面试）：
 * 1. 为什么走 Java 转发而不是 nginx 直连？—— 非流式 + 语义上必须登录
 *    （发笔记本来就要登录）；/ai/** 不在 MvcConfig 白名单里，
 *    LoginInterceptor 天然拦截未登录请求，零配置获得鉴权；
 * 2. 为什么用 Hutool？—— pom 已有 hutool-all，零新增依赖；
 *    RestTemplate 的响应缓冲问题只影响流式（聊天已走 nginx 直连），
 *    非流式转发不值得为此引入 WebClient/webflux；
 * 3. 故障隔离：Python 挂掉返回 Result.fail("AI 服务暂不可用")，
 *    前端体验不崩，排障时先 curl /api/ai/health 即可定位；
 * 4. 30s 超时：LLM 生成标题/润色可能要 5-15 秒，
 *    前端 axios 全局 5s 会在请求级被覆盖，这里同样要放宽。
 */
@Slf4j
@RestController
@RequestMapping("/ai/assist")
public class AiController {

    @Value("${ai.python-url:http://127.0.0.1:8000}")
    private String pythonUrl;

    /**
     * 统一转发：请求体原样透传（Python 端已按统一 Result 信封返回，零改造）。
     */
    private String forward(String action, String body) {
        try {
            HttpResponse resp = HttpRequest.post(pythonUrl + "/api/ai/assist/" + action)
                    .header("Content-Type", "application/json;charset=UTF-8")
                    .body(body)
                    .timeout(30000)
                    .execute();
            if (!resp.isOk()) {
                log.warn("调用 Python AI 服务返回非 200: status={}", resp.getStatus());
                return JSONUtil.toJsonStr(Result.fail("AI 服务异常，请稍后再试"));
            }
            return resp.body();
        } catch (Exception e) {
            log.error("调用 Python AI 服务失败 action={}", action, e);
            return JSONUtil.toJsonStr(Result.fail("AI 服务暂不可用，请稍后再试"));
        }
    }

    @PostMapping(value = "/title", produces = "application/json;charset=UTF-8")
    public String title(@RequestBody String body) {
        return forward("title", body);
    }

    @PostMapping(value = "/polish", produces = "application/json;charset=UTF-8")
    public String polish(@RequestBody String body) {
        return forward("polish", body);
    }

    @PostMapping(value = "/sentiment", produces = "application/json;charset=UTF-8")
    public String sentiment(@RequestBody String body) {
        return forward("sentiment", body);
    }
}
