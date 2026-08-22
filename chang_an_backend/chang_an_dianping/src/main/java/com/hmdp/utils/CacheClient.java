package com.hmdp.utils;

import cn.hutool.core.util.BooleanUtil;
import cn.hutool.core.util.StrUtil;
import cn.hutool.json.JSONObject;
import cn.hutool.json.JSONUtil;
import com.hmdp.entity.Shop;
import lombok.extern.slf4j.Slf4j;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.stereotype.Component;

import javax.annotation.Resource;
import java.time.LocalDateTime;
import java.util.Random;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;
import java.util.function.Function;

import static com.hmdp.utils.RedisConstants.*;
import static com.hmdp.utils.RedisConstants.CACHE_SHOP_TTL;

@Slf4j
@Component
public class CacheClient {
    @Resource
    private StringRedisTemplate stringRedisTemplate;

    private static final ExecutorService CACHE_REBUILD_EXECUTOR = Executors.newFixedThreadPool(10);

    public void set(String key, Object value, Long time, TimeUnit unit){
        stringRedisTemplate.opsForValue().set(key, JSONUtil.toJsonStr(value), time, unit);

    }

    public void setWithLogicalExpire(String key, Object value, Long time, TimeUnit unit){
        // 设置逻辑过期
        RedisData redisData = new RedisData();
        redisData.setExpireTime(LocalDateTime.now().plusSeconds(unit.toSeconds(time)));
        redisData.setData(value);
        stringRedisTemplate.opsForValue().set(key, JSONUtil.toJsonStr(redisData));
    }

    // 缓存穿透
    public <R,ID> R queryWithPassThrough(String keyPrefix, ID id, Class<R> type, Function<ID,R> dbFallback,
                                         Long time, TimeUnit unit) {
        // 从redis查询商铺缓存
        String key = keyPrefix + id;
        String json = stringRedisTemplate.opsForValue().get(key);
        if (StrUtil.isNotBlank(json)) {
            // 判断缓存是否命中
            // 如果命中则返回缓存数据
            return JSONUtil.toBean(json, type);
        }
        // 解决缓存穿透
        // 判断命中是否为空值
        if (json != null) {
            //因为isNotEmpty方法会判断null和""为false，所以这里判断不为null，就可以判断为空
            return null;
        }
        // 未命中则查询数据库
        R r = dbFallback.apply(id);
        if (r == null) {
            // 判断数据库中商铺是否存在
            // 如果不存在则返回失败
            stringRedisTemplate.opsForValue().set(key, "", CACHE_NULL_TTL, TimeUnit.MINUTES);
            return null;
        }
        Random random = new Random();
        long randomTime = random.nextLong(time-10L,time+10L);
        // 如果存在则将商铺数据写入redis并返回
        this.set(key, r, randomTime, unit);
        return r;
    }


    // 逻辑过期解决缓存击穿
    public <R,ID> R queryWithLogicalExpire(String keyPrefix,ID id,Class<R> type,Function<ID,R> dbFallback,Long time, TimeUnit unit) {
        // 从redis查询商铺缓存
        String key = keyPrefix + id;
        String json = stringRedisTemplate.opsForValue().get(key);
        if (StrUtil.isBlank(json)) {
            // 原始黑马代码会出现bug
            R r = dbFallback.apply(id);
            if (r == null) return null;
            setWithLogicalExpire(key, r, time, unit);
            // 判断缓存是否命中
            // 未命中返回null
            return r;
        }
        //命中，先把json反序列化为对象
        RedisData redisData = JSONUtil.toBean(json, RedisData.class);
        JSONObject data = (JSONObject) redisData.getData();
        R r = JSONUtil.toBean(data, type);
        LocalDateTime expireTime = redisData.getExpireTime();
        //判断是否过期
        if(expireTime.isAfter(LocalDateTime.now())){
            //未过期返回店铺信息
            return r;
        }
        //1. 过期就要进行缓存重建
        //1. 获取互斥锁
        String lockKey = LOCK_SHOP_KEY + id;
        //1. 判断是否获取成功
        if(tryLock(lockKey)){
            //上锁后二次确认缓存是否存在
            json = stringRedisTemplate.opsForValue().get(key);
            if (StrUtil.isNotBlank(json)) {
                redisData = JSONUtil.toBean(json, RedisData.class);
                expireTime = redisData.getExpireTime();
                if(expireTime.isAfter(LocalDateTime.now())){
                    // 其他线程已重建，直接返回新缓存
                    data = (JSONObject) redisData.getData();
                    r = JSONUtil.toBean(data, type);
                    unlock(lockKey);
                    return r;
                }
            }
            //1. 成功，开启独立线程实现缓存重建
            CACHE_REBUILD_EXECUTOR.submit(() -> {
                try {
                    R r1 = dbFallback.apply(id);
                    //写入redis
                    setWithLogicalExpire(key, r1, time, unit);
                } catch (Exception e) {
                    throw new RuntimeException(e);
                } finally {
                    unlock(lockKey);
                }
            });
        }
        //1. 返回商铺信息
        return r;
    }

    //获取互斥锁
    private boolean tryLock(String key){
        Boolean flag = stringRedisTemplate.opsForValue().setIfAbsent(key, LOCK_SHOP_KEY, LOCK_SHOP_TTL, TimeUnit.SECONDS);
        //正常返回flag可以会自动拆箱导致空指针的产生 ，所以我们使用BooleanUtil.isTrue方法判断是否为true
        return BooleanUtil.isTrue(flag);
    }

    // 释放互斥锁
    private void unlock(String key){
        stringRedisTemplate.delete(key);
    }
}
