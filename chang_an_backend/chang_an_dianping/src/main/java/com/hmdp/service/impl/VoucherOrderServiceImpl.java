package com.hmdp.service.impl;

import cn.hutool.core.bean.BeanUtil;
import cn.hutool.core.util.BooleanUtil;
import com.hmdp.dto.Result;
import com.hmdp.entity.VoucherOrder;
import com.hmdp.mapper.VoucherOrderMapper;
import com.hmdp.service.ISeckillVoucherService;
import com.hmdp.service.IVoucherOrderService;
import com.baomidou.mybatisplus.extension.service.impl.ServiceImpl;
import com.hmdp.utils.RedisIDWorker;
import com.hmdp.utils.UserHolder;
import lombok.extern.slf4j.Slf4j;
import com.hmdp.entity.SeckillVoucher;
//******** RocketMQ 相关导入（已注释，如需切换回 RocketMQ 请取消注释）********
//import com.hmdp.service.ISeckillMessageService;
//import org.apache.rocketmq.client.producer.SendResult;
//import org.apache.rocketmq.spring.core.RocketMQTemplate;
import org.redisson.api.RLock;
import org.redisson.api.RedissonClient;
import org.springframework.aop.framework.AopContext;
import org.springframework.core.io.ClassPathResource;
import org.springframework.data.redis.connection.stream.*;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.data.redis.core.script.DefaultRedisScript;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import javax.annotation.PostConstruct;
import javax.annotation.Resource;
import java.time.Duration;
import java.util.Collections;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.concurrent.BlockingDeque;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.LinkedBlockingDeque;

/**
 * <p>
 * 服务实现类
 * </p>
 *
 * @author 虎哥
 * @since 2021-12-22
 */
@Service
@Slf4j
public class VoucherOrderServiceImpl extends ServiceImpl<VoucherOrderMapper, VoucherOrder> implements IVoucherOrderService {
    @Resource
    private ISeckillVoucherService seckillVoucherService;

    @Resource
    private RedisIDWorker redisIDWorker;

    @Resource
    private StringRedisTemplate stringRedisTemplate;

    @Resource
    private RedissonClient redissonClient;

    //******** RocketMQ 相关依赖（已注释，如需切换回 RocketMQ 请取消注释）********
    //@Resource
    //private RocketMQTemplate rocketMQTemplate;

    //@Resource
    //private ISeckillMessageService seckillMessageService;

    private static final DefaultRedisScript<Long> SECKILL_SCRIPT;
    static {
        //加载Lua脚本
        SECKILL_SCRIPT = new DefaultRedisScript<>();
        SECKILL_SCRIPT.setLocation(new ClassPathResource("seckill.lua"));
        SECKILL_SCRIPT.setResultType(Long.class);
    }


    private static final ExecutorService SECKILL_ORDER_EXECUTOR = Executors.newSingleThreadExecutor();
    @PostConstruct
    private void init(){
        SECKILL_ORDER_EXECUTOR.submit(new VoucherOrderHandler());
    }
    private class VoucherOrderHandler implements Runnable {
        String queueName="stream.orders";
        @Override
        public void run() {
            while (true) {
                try {
                    //从消息队列中获取下单信息 XREADGROUP GROUP g1 c1 COUNT 1 BLOCK 2000 STREAMS stream.orders >
                    List<MapRecord<String, Object, Object>> list = stringRedisTemplate.opsForStream().read(
                            Consumer.from("g1", "c1"),
                            StreamReadOptions.empty().count(1).block(Duration.ofSeconds(2)),
                            StreamOffset.create(queueName, ReadOffset.lastConsumed())
                    );
                    //判断消息是否获取成功
                    if(list==null||list.isEmpty()){
                        //如果获取失败，说明没有消息，继续下一次循环
                        continue;
                    }
                    //解析消息中的订单
                    MapRecord<String, Object, Object> record = list.get(0);
                    Map<Object, Object> values = record.getValue();
                    VoucherOrder voucherOrder = BeanUtil.fillBeanWithMap(values, new VoucherOrder(), true);
                    //如果获取成功，可以下单
                    handleVoucherOrder(voucherOrder);
                    //ACK确认 SACK stream streams.order g1 id
                    stringRedisTemplate.opsForStream().acknowledge(queueName, "g1", record.getId());
                } catch (Exception e) {
                    log.error("处理订单异常",e);
                    handlePendingList();
                }
            }
        }

        private void handlePendingList() {
            while (true) {
                try {
                    //从pending-list中获取下单信息 XREADGROUP GROUP g1 c1 COUNT 1 STREAMS streams.order 0
                    List<MapRecord<String, Object, Object>> list = stringRedisTemplate.opsForStream().read(
                            Consumer.from("g1", "c1"),
                            StreamReadOptions.empty().count(1),
                            StreamOffset.create(queueName, ReadOffset.from("0"))
                    );
                    //判断消息是否获取成功
                    if(list==null||list.isEmpty()){
                        //如果获取失败，说明pending-list没有异常消息，结束循环
                        break;
                    }
                    //解析消息中的订单
                    MapRecord<String, Object, Object> record = list.get(0);
                    Map<Object, Object> values = record.getValue();
                    VoucherOrder voucherOrder = BeanUtil.fillBeanWithMap(values, new VoucherOrder(), true);
                    //如果获取成功，可以下单
                    handleVoucherOrder(voucherOrder);
                    //ACK确认 SACK stream streams.order g1 id
                    stringRedisTemplate.opsForStream().acknowledge(queueName, "g1", record.getId());
                } catch (Exception e) {
                    log.error("处理pending-list订单异常",e);
                    try {
                        Thread.sleep(20);
                    } catch (InterruptedException ex) {
                        ex.printStackTrace();
                    }
                }
            }
        }
    }
    /*private BlockingDeque<VoucherOrder> orderTasks = new LinkedBlockingDeque<>(1024*1024);

    private class VoucherOrderHandler implements Runnable {
        @Override
        public void run() {
            while (true) {
                try {
                    //从阻塞队列中获取下单信息
                    VoucherOrder voucherOrder = orderTasks.take();
                    //创建订单
                    handleVoucherOrder(voucherOrder);
                } catch (InterruptedException e) {
                    log.error("处理订单异常",e);
                }
            }
        }
    }*/
    public void handleVoucherOrder(VoucherOrder voucherOrder) {
        //锁加到事务外面避免脏读
        //获取锁对象
        Long userId = voucherOrder.getUserId();
        RLock lock = redissonClient.getLock("lock:order" + userId);
        //判断是否获取成功
        if(!lock.tryLock()){
            log.error("不允许重复下单");
            return;
        }

        try {
            proxy.createVoucherOrder(voucherOrder);
        } finally {
            lock.unlock();
        }
    }

    private IVoucherOrderService proxy;

    /**
     * 检查并初始化Redis库存
     */
    private void checkAndInitStock(Long voucherId) {
        String stockKey = "seckill:stock:" + voucherId;
        //检查Redis中是否有库存
        Boolean exists = stringRedisTemplate.hasKey(stockKey);
        if (BooleanUtil.isFalse(exists)) {
            //从数据库读取库存
            SeckillVoucher voucher = seckillVoucherService.getById(voucherId);
            if (voucher != null && voucher.getStock() > 0) {
                //设置到Redis
                stringRedisTemplate.opsForValue().set(stockKey, voucher.getStock().toString());
            }
        }
    }

    @Override
    public Result seckillVoucher(Long voucherId) {
        //获取用户
        Long userId = UserHolder.getUser().getId();
        //获取订单id
        Long orderId = redisIDWorker.nextId("order");
        
        //先检查并初始化Redis库存
        checkAndInitStock(voucherId);
        
        //执行Lua脚本
        Long result = stringRedisTemplate.execute(
                SECKILL_SCRIPT,
                Collections.emptyList(),
                voucherId.toString(),
                userId.toString(),
                orderId.toString()
        );
        //判断结果是否为0
        int r=result.intValue();
        if(r != 0){
            //不是0代表没有购买资格
            return Result.fail(r==1?"库存不足":"不能重复下单");
        }

        // ===== Redis Stream 版本：发送消息到 Redis Stream =====
        Map<String, Object> msgMap = new HashMap<>();
        msgMap.put("userId", userId);
        msgMap.put("voucherId", voucherId);
        msgMap.put("id", orderId);
        stringRedisTemplate.opsForStream().add("stream.orders", msgMap);

        //******** RocketMQ 版本（已注释，如需切换回 RocketMQ 请取消注释）********
        /*
        try {
            Map<String, Object> rocketMsgMap = new HashMap<>();
            rocketMsgMap.put("userId", userId);
            rocketMsgMap.put("voucherId", voucherId);
            rocketMsgMap.put("id", orderId);
            SendResult sendResult = rocketMQTemplate.syncSend("seckill-order-topic", rocketMsgMap);
            log.info("RocketMQ消息发送成功，orderId={}, status={}", orderId, sendResult.getSendStatus());
        } catch (Exception e) {
            // 如果 RocketMQ 发送失败，使用本地消息表补偿
            log.error("RocketMQ消息发送失败，使用本地消息表补偿：orderId={}", orderId, e);
            seckillMessageService.sendWithLocalMessage(userId, voucherId, orderId);
        }
        */

        //获取代理对象（事务）
        proxy = (IVoucherOrderService) AopContext.currentProxy();
        return Result.ok(orderId);
    }
    /*@Override
    public Result seckillVoucher(Long voucherId) {
        //获取用户
        Long userId = UserHolder.getUser().getId();
        //执行Lua脚本
        Long result = stringRedisTemplate.execute(
                SECKILL_SCRIPT,
                Collections.emptyList(),
                voucherId.toString(),userId.toString()
        );
        //判断结果是否为0
        int r=result.intValue();
        if(r != 0){
            //不是0代表没有购买资格
            return Result.fail(r==1?"库存不足":"不能重复下单");
        }
        //为0代表有购买资格，把下单信息保存到阻塞队列
        Long orderId = redisIDWorker.nextId("order");
        VoucherOrder voucherOrder = new VoucherOrder();
        voucherOrder.setId(orderId);
        voucherOrder.setUserId(userId);
        voucherOrder.setVoucherId(voucherId);
        //保存阻塞队列
        orderTasks.add(voucherOrder);
        //获取代理对象（事务）
        proxy = (IVoucherOrderService) AopContext.currentProxy();
        return Result.ok(orderId);
    }*/
    /*@Override
    public Result seckillVoucher(Long voucherId) {
        //查询优惠劵信息
        SeckillVoucher voucher = seckillVoucherService.getById(voucherId);
        //判断秒杀是否开始
        if (voucher.getBeginTime().isAfter(LocalDateTime.now())) {
            //没有开始的话返回异常结果
            return Result.fail("秒杀未开始");
        }
        //判断秒杀是否结束
        if (voucher.getEndTime().isBefore(LocalDateTime.now())) {
            //结束的话返回异常结果
            return Result.fail("秒杀已结束");
        }
        //开始的话判断库存是否充足
        if (voucher.getStock() < 1) {
            //库存不充足的话返回异常结果
            return Result.fail("库存不足");
        }

        Long userId = UserHolder.getUser().getId();
        //锁加到事务外面避免脏读
        //获取锁对象
        //SimpleRedisLock simpleRedisLock = new SimpleRedisLock("order"+userId, stringRedisTemplate);
        RLock lock = redissonClient.getLock("lock:order" + userId);
        //判断是否获取成功
        if(!lock.tryLock()){
            return Result.fail("请稍后再试");
        }

        try {
            //获取代理对象（事务）
            IVoucherOrderService proxy = (IVoucherOrderService) AopContext.currentProxy();
            return proxy.createVoucherOrder(voucherId);
        } finally {
            lock.unlock();
        }

    }*/

    @Transactional
    public void createVoucherOrder(VoucherOrder voucherOrder) {
        // .一人一单
        Long userId = voucherOrder.getUserId();

        // .查询订单
        long count = query().eq("user_id", userId).eq("voucher_id", voucherOrder.getVoucherId()).count();
        if (count > 0) {
            //查询到订单的话返回异常结果
            log.error("您已购买过该优惠劵");
            return;
        }
        // .判断用户是否存在
        //库存充足的话扣减库存
        boolean success = seckillVoucherService.update()
                .setSql("stock=stock-1")
                .eq("voucher_id", voucherOrder.getVoucherId()).gt("stock", 0)
                .update();
        if (!success) {
            //扣减库存失败的话返回异常结果
            log.error("库存不足");
            return;
        }


        //保存订单
        save(voucherOrder);

    }

}