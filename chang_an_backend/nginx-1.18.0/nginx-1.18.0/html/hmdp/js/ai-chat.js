// AI 聊天页：fetch + ReadableStream 手写 SSE 解析。
//
// 为什么不用 axios（面试点）：
// 1. common.js 全局 timeout=5000ms，流式回答动辄十几秒，必被超时打断；
// 2. 响应拦截器只认 {success, data} JSON 信封，不认识 text/event-stream；
// 3. axios 基于 XHR，拿不到增量响应体——流式解析必须 fetch + ReadableStream。
//
// SSE 解析要点：
// 1. TextDecoder({stream: true})：UTF-8 汉字是 3 字节，网络分块可能把一个字
//    劈成两半，stream 模式会缓存半个字符等下个 chunk 拼齐；
// 2. 事件以空行（\n\n）分隔：event: xxx / data: xxx 成对出现；
// 3. data 是 JSON 字符串，统一 JSON.parse（sources 是结构数据）。
const app = new Vue({
  el: "#app",
  data: {
    messages: [],   // {role: "user"|"assistant", text, sources: [], error, streaming}
    input: "",
    sending: false,
    // 会话 id：每次打开页面新建一个会话（阶段 2 的多轮改写靠它关联上下文）
    sessionId: "s" + Date.now() + "-" + Math.floor(Math.random() * 10000),
  },
  created() {
    // 聊天接口 nginx 直连 Python 不校验登录，前端先自查 token（与"发笔记需登录"语义一致）
    if (!sessionStorage.getItem("token")) {
      location.href = "/login.html";
    }
  },
  methods: {
    async send() {
      const text = this.input.trim();
      if (!text || this.sending) return;
      this.sending = true;
      this.messages.push({role: "user", text: text, sources: [], streaming: false});
      this.input = "";
      // 助手占位消息：流式增量往里填
      const reply = {role: "assistant", text: "", sources: [], tools: [], error: "", streaming: true};
      this.messages.push(reply);
      this.scrollToBottom();
      try {
        const resp = await fetch("/api/ai/chat", {
          method: "POST",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify({session_id: this.sessionId, message: text, mode: "auto"}),
        });
        if (!resp.ok) {
          reply.error = "服务异常 HTTP " + resp.status;
          reply.streaming = false;
          return;
        }
        await this.parseSse(resp.body.getReader(), reply);
      } catch (e) {
        reply.error = "网络异常：" + e.message;
      } finally {
        reply.streaming = false;
        this.sending = false;
        this.scrollToBottom();
      }
    },
    async parseSse(reader, reply) {
      const decoder = new TextDecoder("utf-8");
      let buf = "";
      while (true) {
        const {done, value} = await reader.read();
        if (done) break;
        buf += decoder.decode(value, {stream: true});
        // 按空行切出完整事件帧
        let idx;
        while ((idx = buf.indexOf("\n\n")) !== -1) {
          this.handleFrame(buf.slice(0, idx), reply);
          buf = buf.slice(idx + 2);
        }
      }
      buf += decoder.decode(); // flush：吐出缓存里的半个汉字
      if (buf.trim()) this.handleFrame(buf, reply);
    },
    handleFrame(frame, reply) {
      let event = "message", dataStr = "";
      frame.split("\n").forEach(line => {
        if (line.startsWith("event: ")) event = line.slice(7).trim();
        else if (line.startsWith("data: ")) dataStr += line.slice(6);
      });
      if (!dataStr) return;
      let payload;
      try {
        payload = JSON.parse(dataStr);
      } catch (e) {
        return;
      }
      if (event === "sources") {
        reply.sources = payload || [];
      } else if (event === "tool_call") {
        // Agent 工具调用状态：显示"正在查店铺/查券…"
        reply.tools.push(payload);
      } else if (event === "delta") {
        reply.text += payload;  // 打字机：逐段拼接
        this.scrollToBottom();
      } else if (event === "error") {
        reply.error = payload.message || "AI 服务出错";
      }
      // event === "done" 无需处理：结束由 finally 统一收尾
    },
    toolLabel(name) {
      const map = {
        search_shops_by_name: "按名称搜索店铺",
        list_shops_by_type: "按分类查询店铺",
        get_shop_detail: "查询店铺详情",
        list_vouchers: "实时查询优惠券",
        search_blogs: "搜索探店笔记",
        search_knowledge: "搜索文旅知识库",
      };
      return map[name] || name;
    },
    toSource(s) {
      // 来源卡片跳转：店铺/券 → 店铺详情；笔记 → 笔记详情；语料无链接
      if (s.type === "shop" || s.type === "voucher") {
        location.href = "/shop-detail.html?id=" + s.shop_id;
      } else if (s.type === "blog") {
        location.href = "/blog-detail.html?id=" + s.shop_id;
      }
    },
    scrollToBottom() {
      this.$nextTick(() => {
        const el = document.getElementById("chatBody");
        if (el) el.scrollTop = el.scrollHeight;
      });
    },
  }
})
