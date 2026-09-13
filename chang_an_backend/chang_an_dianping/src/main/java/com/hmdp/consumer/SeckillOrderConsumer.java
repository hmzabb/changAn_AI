package com.hmdp.consumer;

//******** RocketMQ 消费者（已注释，当前使用 Redis Stream 方案，如需切换回 RocketMQ 请取消注释）********
//import com.hmdp.entity.VoucherOrder;
//import com.hmdp.service.impl.VoucherOrderServiceImpl;
//import lombok.extern.slf4j.Slf4j;
//import org.apache.rocketmq.spring.annotation.RocketMQMessageListener;
//import org.apache.rocketmq.spring.core.RocketMQListener;
//import org.springframework.stereotype.Component;
//
//import javax.annotation.Resource;
//import java.util.Map;

//******** RocketMQ 消费者（已注释，当前使用 Redis Stream 方案，如需切换回 RocketMQ 请取消注释）********
//@Slf4j
//@Component
//@RocketMQMessageListener(
//        topic = "seckill-order-topic",
//        consumerGroup = "seckill-consumer-group",
//        consumeMode = org.apache.rocketmq.spring.annotation.ConsumeMode.CONCURRENTLY,
//        messageModel = org.apache.rocketmq.spring.annotation.MessageModel.CLUSTERING
//)
//public class SeckillOrderConsumer implements RocketMQListener<Map<String, Object>> {
//
//    @Resource
//    private VoucherOrderServiceImpl voucherOrderService;
//
//    @Override
//    public void onMessage(Map<String, Object> msgMap) {
//        try {
//            Long userId = Long.valueOf(msgMap.get("userId").toString());
//            Long voucherId = Long.valueOf(msgMap.get("voucherId").toString());
//            Long orderId = Long.valueOf(msgMap.get("id").toString());
//
//            log.info("收到RocketMQ秒杀订单消息：userId={}, voucherId={}, orderId={}",
//                    userId, voucherId, orderId);
//
//            VoucherOrder voucherOrder = new VoucherOrder()
//                    .setId(orderId)
//                    .setUserId(userId)
//                    .setVoucherId(voucherId);
//
//            voucherOrderService.handleVoucherOrder(voucherOrder);
//
//            log.info("RocketMQ订单创建成功：orderId={}", orderId);
//        } catch (Exception e) {
//            log.error("处理RocketMQ秒杀订单失败", e);
//            throw new RuntimeException("处理失败，触发RocketMQ重试", e);
//        }
//    }
//}