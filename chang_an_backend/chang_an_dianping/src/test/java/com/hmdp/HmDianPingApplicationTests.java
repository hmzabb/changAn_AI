package com.hmdp;

import com.hmdp.entity.Shop;
import com.hmdp.service.impl.ShopServiceImpl;
import org.junit.jupiter.api.Test;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.data.geo.Point;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.data.redis.connection.RedisGeoCommands.GeoLocation;

import javax.annotation.Resource;
import java.awt.*;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.stream.Collectors;

import static com.hmdp.utils.RedisConstants.SHOP_GEO_KEY;

@SpringBootTest
class HmDianPingApplicationTests {
    @Resource
    private ShopServiceImpl shopService;
    @Resource
    private StringRedisTemplate stringRedisTemplate;

    @Test
    void loadShopData() {
        //1.查询店铺信息
        List<Shop> list = shopService.list();
        //2.将店铺分组，按照typeId分组，typeId一致的放到一个集合
        Map<Long, List<Shop>> map = list.stream().collect(Collectors.groupingBy(Shop::getTypeId));
        //3.分批写入redis
        for(Map.Entry<Long, List<Shop>> entry : map.entrySet()){
            //3.1.获取类型id
            Long typeId = entry.getKey();
            String key=SHOP_GEO_KEY+typeId;
            //3.2.获取同类型店铺集合
            List<Shop> value = entry.getValue();
            List<GeoLocation<String>> locations = new ArrayList<>(value.size());
            //3.3.写入redis
            for (Shop shop : value) {
                locations.add(new GeoLocation<>(
                        shop.getId().toString(),
                        new Point(shop.getX(), shop.getY())
                ));
            }
            stringRedisTemplate.opsForGeo().add(key, locations);
        }
    }
}
