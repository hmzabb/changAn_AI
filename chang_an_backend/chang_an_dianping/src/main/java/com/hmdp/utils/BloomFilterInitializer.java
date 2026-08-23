package com.hmdp.utils;

import com.hmdp.entity.Shop;
import com.hmdp.service.IShopService;
import lombok.extern.slf4j.Slf4j;
import org.springframework.boot.ApplicationArguments;
import org.springframework.boot.ApplicationRunner;
import org.springframework.stereotype.Component;

import javax.annotation.Resource;
import java.util.List;
import java.util.stream.Collectors;

/**
 * 布隆过滤器初始化器
 * 在Spring Boot启动完成后自动执行，将数据库中所有商铺ID加载到布隆过滤器
 */
@Slf4j
@Component
public class BloomFilterInitializer implements ApplicationRunner {

    @Resource
    private CacheClient cacheClient;

    @Resource
    private IShopService shopService;

    @Override
    public void run(ApplicationArguments args) {
        log.info("开始初始化布隆过滤器...");
        // 初始化布隆过滤器：预计10万条数据，误判率1%
        cacheClient.initShopBloomFilter(100000L, 0.01);
        // 从数据库加载所有已存在的商铺ID
        List<Shop> shops = shopService.list();
        List<Long> ids = shops.stream().map(Shop::getId).collect(Collectors.toList());
        cacheClient.loadShopIdsToBloomFilter(ids);
        log.info("布隆过滤器初始化完成，共加载 {} 个商铺ID", ids.size());
    }
}