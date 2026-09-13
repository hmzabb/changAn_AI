package com.hmdp.controller;

//******** RocketMQ 测试控制器（已注释，当前使用 Redis Stream 方案，如需切换回 RocketMQ 请取消注释）********
//import com.hmdp.dto.Result;
//import lombok.extern.slf4j.Slf4j;
//import org.apache.rocketmq.spring.core.RocketMQTemplate;
//import org.springframework.web.bind.annotation.GetMapping;
//import org.springframework.web.bind.annotation.RequestMapping;
//import org.springframework.web.bind.annotation.RestController;
//
//import javax.annotation.Resource;
//import java.util.HashMap;
//import java.util.Map;
//
//@Slf4j
//@RestController
//@RequestMapping("/test")
//public class TestController {
//
//    @Resource
//    private RocketMQTemplate rocketMQTemplate;
//
//    @GetMapping("/rocketmq")
//    public Result testRocketMQ() {
//        try {
//            Map<String, Object> msgMap = new HashMap<>();
//            msgMap.put("test", "hello rocketmq");
//            msgMap.put("time", System.currentTimeMillis());
//            
//            rocketMQTemplate.syncSend("test-topic", msgMap);
//            log.info("测试消息发送成功");
//            return Result.ok("RocketMQ 测试成功！");
//        } catch (Exception e) {
//            log.error("RocketMQ 测试失败", e);
//            return Result.fail("RocketMQ 测试失败：" + e.getMessage());
//        }
//    }
//}