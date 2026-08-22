-- 修复后的完整脚本
-- 1.参数列表
-- 1.1 优惠券id
local voucherId = ARGV[1]
-- 1.2 用户id
local userId = ARGV[2]  -- 统一变量名
-- 1.3 订单id
local orderId = ARGV[3]
-- 2.数据key
local stockKey = "seckill:stock:" .. voucherId
local orderKey = "seckill:order:" .. voucherId

-- 3.脚本业务
-- 3.1.判断库存是否充足
local stock = redis.call('get', stockKey)
if(not stock or tonumber(stock) <= 0) then  -- 添加nil判断
    return 1  -- 库存不足
end
-- 3.2.判断用户是否已下单
if(redis.call('sismember', orderKey, userId) == 1) then
    return 2  -- 重复下单
end
-- 3.3.扣库存
redis.call('incrby', stockKey, -1)
-- 3.4.下单
redis.call('sadd', orderKey, userId)
-- 3.5.发送消息到队列中
redis.call('xadd', 'stream.orders','*','userId',userId,'voucherId',voucherId,'id',orderId)
return 0  -- 成功