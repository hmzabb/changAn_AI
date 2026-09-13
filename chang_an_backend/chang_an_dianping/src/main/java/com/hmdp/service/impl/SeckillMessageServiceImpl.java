package com.hmdp.service.impl;

//******** RocketMQ 本地消息表服务（已注释，当前使用 Redis Stream 方案，如需切换回 RocketMQ 请取消注释）********
//import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
//import com.baomidou.mybatisplus.extension.service.impl.ServiceImpl;
//import com.hmdp.entity.SeckillMessage;
//import com.hmdp.mapper.SeckillMessageMapper;
//import com.hmdp.service.ISeckillMessageService;
//import lombok.extern.slf4j.Slf4j;
//import org.apache.rocketmq.client.producer.SendResult;
//import org.apache.rocketmq.spring.core.RocketMQTemplate;
//import org.springframework.scheduling.annotation.Scheduled;
//import org.springframework.stereotype.Service;
//import org.springframework.transaction.annotation.Transactional;
//
//import javax.annotation.Resource;
//import java.util.HashMap;
//import java.util.List;
//import java.util.Map;

//******** RocketMQ 本地消息表服务（已注释，当前使用 Redis Stream 方案，如需切换回 RocketMQ 请取消注释）********
//@Slf4j
//@Service
//public class SeckillMessageServiceImpl extends ServiceImpl<SeckillMessageMapper, SeckillMessage> implements ISeckillMessageService {
//
//    @Resource
//    private RocketMQTemplate rocketMQTemplate;
//
//    private static final String TOPIC = "seckill-order-topic";
//
//    @Override
//    @Transactional(rollbackFor = Exception.class)
//    public void sendWithLocalMessage(Long userId, Long voucherId, Long orderId) {
//        SeckillMessage message = new SeckillMessage()
//                .setUserId(userId)
//                .setVoucherId(voucherId)
//                .setOrderId(orderId)
//                .setStatus(0)
//                .setRetryCount(0);
//        this.save(message);
//
//        try {
//            Map<String, Object> msgMap = new HashMap<>();
//            msgMap.put("userId", userId);
//            msgMap.put("voucherId", voucherId);
//            msgMap.put("id", orderId);
//
//            SendResult result = rocketMQTemplate.syncSend(TOPIC, msgMap);
//            message.setStatus(1);
//            this.updateById(message);
//            log.info("RocketMQ消息发送成功：orderId={}", orderId);
//        } catch (Exception e) {
//            message.setStatus(2);
//            message.setErrorMsg(e.getMessage());
//            this.updateById(message);
//            log.error("RocketMQ消息发送失败，等待补偿：orderId={}", orderId, e);
//        }
//    }
//
//    @Override
//    @Scheduled(fixedDelay = 30000)
//    public void retryFailedMessages() {
//        List<SeckillMessage> failedMessages = this.list(
//                new LambdaQueryWrapper<SeckillMessage>()
//                        .eq(SeckillMessage::getStatus, 2)
//                        .lt(SeckillMessage::getRetryCount, 3)
//                        .orderByAsc(SeckillMessage::getCreateTime)
//                        .last("LIMIT 100")
//        );
//
//        if (failedMessages.isEmpty()) {
//            return;
//        }
//
//        log.info("开始补偿发送失败的RocketMQ消息，共 {} 条", failedMessages.size());
//
//        for (SeckillMessage msg : failedMessages) {
//            try {
//                Map<String, Object> msgMap = new HashMap<>();
//                msgMap.put("userId", msg.getUserId());
//                msgMap.put("voucherId", msg.getVoucherId());
//                msgMap.put("id", msg.getOrderId());
//
//                SendResult result = rocketMQTemplate.syncSend(TOPIC, msgMap);
//                msg.setStatus(1);
//                msg.setRetryCount(msg.getRetryCount() + 1);
//                this.updateById(msg);
//                log.info("补偿发送成功：orderId={}, 重试次数={}", msg.getOrderId(), msg.getRetryCount());
//            } catch (Exception e) {
//                msg.setRetryCount(msg.getRetryCount() + 1);
//                msg.setErrorMsg(e.getMessage());
//                this.updateById(msg);
//                log.error("补偿发送失败：orderId={}, 重试次数={}", msg.getOrderId(), msg.getRetryCount(), e);
//            }
//        }
//    }
//}