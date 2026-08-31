-- RocketMQ 本地消息表（用于消息发送失败时的补偿）
CREATE TABLE IF NOT EXISTS `tb_seckill_message` (
  `id` bigint NOT NULL AUTO_INCREMENT,
  `user_id` bigint NOT NULL COMMENT '用户ID',
  `voucher_id` bigint NOT NULL COMMENT '优惠券ID',
  `order_id` bigint NOT NULL COMMENT '订单ID',
  `status` tinyint NOT NULL DEFAULT 0 COMMENT '0=待发送 1=已发送 2=发送失败',
  `retry_count` int NOT NULL DEFAULT 0 COMMENT '重试次数',
  `error_msg` varchar(500) DEFAULT NULL COMMENT '错误信息',
  `create_time` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `update_time` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  KEY `idx_status` (`status`),
  KEY `idx_order_id` (`order_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='秒杀消息本地表';