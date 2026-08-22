-- KEYS[1]: 锁的 key
-- ARGV[1]: 当前线程标识

if (redis.call('GET', KEYS[1]) == ARGV[1]) then
    -- 一致，则删除锁
    return redis.call('DEL', KEYS[1])
end
-- 不一致，则直接返回
return 0