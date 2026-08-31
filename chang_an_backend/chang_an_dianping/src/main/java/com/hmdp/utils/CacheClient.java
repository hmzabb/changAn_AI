package com.hmdp.utils;

import cn.hutool.core.util.BooleanUtil;
import cn.hutool.core.util.StrUtil;
import cn.hutool.json.JSONObject;
import cn.hutool.json.JSONUtil;
import com.hmdp.entity.Shop;
import lombok.extern.slf4j.Slf4j;
import org.redisson.api.RBloomFilter;
import org.redisson.api.RedissonClient;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.stereotype.Component;

import javax.annotation.Resource;
import java.time.LocalDateTime;
import java.util.List;
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

    @Resource
    private RedissonClient redissonClient;

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

    // 综合方案：同时解决缓存穿透、击穿、雪崩三大问题
    public <R,ID> R queryWithAllProtection(String keyPrefix, ID id, Class<R> type, Function<ID,R> dbFallback,
                                           Long time, TimeUnit unit) {
        // ========== 第一道防线：布隆过滤器防穿透 ==========
        RBloomFilter<Long> bloomFilter = redissonClient.getBloomFilter(BLOOM_SHOP_KEY);
        if (bloomFilter.isExists() && !bloomFilter.contains((Long) id)) {
            log.debug("布隆过滤器拦截不存在的ID: {}", id);
            return null;
        }

        String key = keyPrefix + id;
        String json = stringRedisTemplate.opsForValue().get(key);

        // ========== 缓存未命中 ==========
        if (StrUtil.isBlank(json)) {
            // 查数据库
            R r = dbFallback.apply(id);
            if (r == null) {
                // 数据库也不存在 → 空值缓存防穿透
                stringRedisTemplate.opsForValue().set(key, "", CACHE_NULL_TTL, TimeUnit.MINUTES);
                return null;
            }
            // 数据库存在 → 写入逻辑过期缓存（防击穿）+ TTL随机化（防雪崩）
            Random random = new Random();
            long randomTime = random.nextLong(time - 10L, time + 10L);
            setWithLogicalExpire(key, r, randomTime, unit);
            return r;
        }

        // ========== 缓存命中 ==========
        RedisData redisData = JSONUtil.toBean(json, RedisData.class);
        JSONObject data = (JSONObject) redisData.getData();
        R r = JSONUtil.toBean(data, type);
        LocalDateTime expireTime = redisData.getExpireTime();

        // 判断逻辑过期时间
        if (expireTime.isAfter(LocalDateTime.now())) {
            // 未过期 → 直接返回
            return r;
        }

        // ========== 已过期 → 防击穿逻辑 ==========
        String lockKey = LOCK_SHOP_KEY + id;
        if (tryLock(lockKey)) {
            // 双重检查
            json = stringRedisTemplate.opsForValue().get(key);
            if (StrUtil.isNotBlank(json)) {
                redisData = JSONUtil.toBean(json, RedisData.class);
                expireTime = redisData.getExpireTime();
                if (expireTime.isAfter(LocalDateTime.now())) {
                    // 其他线程已重建 → 直接返回
                    data = (JSONObject) redisData.getData();
                    r = JSONUtil.toBean(data, type);
                    unlock(lockKey);
                    return r;
                }
            }

            // 异步重建缓存
            CACHE_REBUILD_EXECUTOR.submit(() -> {
                try {
                    R r1 = dbFallback.apply(id);
                    if (r1 != null) {
                        Random random = new Random();
                        long randomTime = random.nextLong(time - 10L, time + 10L);
                        setWithLogicalExpire(key, r1, randomTime, unit);
                    }
                } catch (Exception e) {
                    log.error("缓存重建失败", e);
                } finally {
                    unlock(lockKey);
                }
            });
        }

        // 返回旧数据（保证用户体验）
        return r;
    }

    // 初始化布隆过滤器
    public void initShopBloomFilter(long expectedInsertions, double falseProbability) {
        RBloomFilter<Long> bloomFilter = redissonClient.getBloomFilter(BLOOM_SHOP_KEY);
        boolean initialized = bloomFilter.tryInit(expectedInsertions, falseProbability);
        if (initialized) {
            log.info("布隆过滤器初始化成功，预计插入量: {}, 误判率: {}", expectedInsertions, falseProbability);
        } else {
            log.info("布隆过滤器已存在，跳过初始化");
        }
    }


    // 批量加载ID到布隆过滤器
    public void loadShopIdsToBloomFilter(List<Long> ids) {
        RBloomFilter<Long> bloomFilter = redissonClient.getBloomFilter(BLOOM_SHOP_KEY);
        if (!bloomFilter.isExists()) {
            log.warn("布隆过滤器未初始化，无法加载数据");
            return;
        }
        for (Long id : ids) {
            bloomFilter.add(id);
        }
        log.info("已向布隆过滤器加载 {} 个ID", ids.size());
    }

    // 新增商铺时同步更新布隆过滤器
    public void addToShopBloomFilter(Long id) {
        RBloomFilter<Long> bloomFilter = redissonClient.getBloomFilter(BLOOM_SHOP_KEY);
        if (bloomFilter.isExists()) {
            bloomFilter.add(id);
        }
    }


    // 逻辑过期解决缓存击穿
    public <R,ID> R queryWithLogicalExpire(String keyPrefix,ID id,Class<R> type,Function<ID,R> dbFallback,Long time,
                                           TimeUnit unit) {
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