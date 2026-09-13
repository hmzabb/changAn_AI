package com.hmdp.service;

import com.baomidou.mybatisplus.extension.service.IService;
import com.hmdp.entity.SeckillMessage;

public interface ISeckillMessageService extends IService<SeckillMessage> {

    void sendWithLocalMessage(Long userId, Long voucherId, Long orderId);

    void retryFailedMessages();
}