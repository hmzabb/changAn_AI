package com.hmdp.service.impl;

import cn.hutool.core.bean.BeanUtil;
import cn.hutool.core.lang.UUID;
import cn.hutool.core.util.RandomUtil;
import com.baomidou.mybatisplus.extension.service.impl.ServiceImpl;
import com.hmdp.dto.LoginFormDTO;
import com.hmdp.dto.Result;
import com.hmdp.dto.UserDTO;
import com.hmdp.entity.User;
import com.hmdp.mapper.UserMapper;
import com.hmdp.service.IUserService;
import com.hmdp.utils.RegexUtils;
import com.hmdp.utils.SystemConstants;
import com.hmdp.utils.UserHolder;
import lombok.extern.slf4j.Slf4j;
import org.springframework.data.redis.connection.BitFieldSubCommands;
import org.springframework.data.redis.core.RedisTemplate;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.stereotype.Service;

import javax.annotation.Resource;
import javax.servlet.http.HttpSession;

import java.time.LocalDateTime;
import java.time.format.DateTimeFormatter;
import java.util.List;
import java.util.Random;
import java.util.concurrent.TimeUnit;

import static com.hmdp.utils.RedisConstants.*;

/**
 * <p>
 * 服务实现类
 * </p>
 *
 * @author 虎哥
 * @since 2021-12-22
 */
@Slf4j
@Service
public class UserServiceImpl extends ServiceImpl<UserMapper, User> implements IUserService {
    @Resource
    private StringRedisTemplate stringRedisTemplate;
    @Override
    public Result sendCode(String phone, HttpSession session) {
        //  发送短信验证码并保存验证码
        // 检查手机号是否合法
        if(RegexUtils.isPhoneInvalid(phone)){
            //如果手机号格式错误，返回失败
            return Result.fail("手机号格式错误");
        }
        //如果手机号格式正确，生成验证码
        String code = RandomUtil.randomNumbers(6);
        //将验证码保存到redis中
        stringRedisTemplate.opsForValue().set(LOGIN_CODE_KEY+phone,code,LOGIN_CODE_TTL, TimeUnit.MINUTES);
        //发送短信验证码
        log.info("验证码发送成功，验证码：{}",code);
        return Result.ok();
    }

    @Override
    public Result login(LoginFormDTO loginForm, HttpSession session) {
        //  实现登录功能
        // 校验手机号
        String phone = loginForm.getPhone();
        if(RegexUtils.isPhoneInvalid(phone)){
            //如果手机号格式错误，返回失败
            return Result.fail("手机号格式错误");
        }
        // 从redis中获取验证码，校验验证码
        String code = loginForm.getCode();
        String cacheCode = stringRedisTemplate.opsForValue().get(LOGIN_CODE_KEY+phone);
        if(cacheCode==null||!cacheCode.equals(code)){
            // 不一致直接报错
            return Result.fail("验证码错误");
        }
        // 一致，根据手机号查询用户是否存在
        User user = query().eq("phone", phone).one();
        if(user==null){
            // 不存在，创建用户并保存
            user=creatUserWithPhone(phone);
        }
        //将新创建的用户保存到redis中
        //随机生成一个token
        String token = UUID.randomUUID().toString(true);
        //将一个user转换成hash存储
        UserDTO userDTO = BeanUtil.copyProperties(user, UserDTO.class);
        String tokenKey = LOGIN_USER_KEY+token;
        stringRedisTemplate.opsForHash().putAll(tokenKey, userDTO.toMap());
        //设置有效期
        stringRedisTemplate.expire(tokenKey,LOGIN_USER_TTL, TimeUnit.MINUTES);
        // 返回token
        return Result.ok(token);
    }

    @Override
    public Result sign() {
        //1.获取用户信息
        Long id = UserHolder.getUser().getId();
        //2.获取日期
        LocalDateTime now = LocalDateTime.now();
        //3.拼接key
        String keySuffix=now.format(DateTimeFormatter.ofPattern(":yyyyMM"));
        String key = USER_SIGN_KEY+id+keySuffix;
        //4.当前是本月的第几天
        int dayOfMonth = now.getDayOfMonth();
        //5.存入redis
        stringRedisTemplate.opsForValue().setBit(key,dayOfMonth,true);
        return Result.ok();
    }

    @Override
    public Result signCount() {
        //1.获取用户信息
        Long id = UserHolder.getUser().getId();
        //2.获取日期
        LocalDateTime now = LocalDateTime.now();
        //3.拼接key
        String keySuffix=now.format(DateTimeFormatter.ofPattern(":yyyyMM"));
        String key = USER_SIGN_KEY+id+keySuffix;
        //4.当前是本月的第几天
        int dayOfMonth = now.getDayOfMonth();
        //5.获取本月截至今天为止的所有的签到记录，返回的是一个十进制数字
        List<Long> result = stringRedisTemplate.opsForValue().bitField(
                key,
                BitFieldSubCommands.create().get(BitFieldSubCommands.BitFieldType.unsigned(dayOfMonth)).valueAt(0)
        );
        if(result==null||result.isEmpty()){
            //没有任何签到结果
            return Result.ok(0L);
        }
        Long num = result.get(0);
        if(num==null||num==0){
            return Result.ok(0L);
        }
        //6.循环遍历
        int count=0;
        while(true){
            //6.1.让这个数字与1做与运算，得到数字的最后一个bit位
            //判断这个bit位是否为0
            if((num&1)==0){
                //如果为0，说明没有签到，跳出循环
                break;
            }else{
                //如果不为0，说明有签到，计数器加1
                count++;
            }
            num>>>=1;
        }
        return Result.ok(count);
    }

    private User creatUserWithPhone(String phone) {
        User user = new User();
        user.setPhone(phone);
        user.setNickName(SystemConstants.USER_NICK_NAME_PREFIX+RandomUtil.randomString(6));
        //保存用户
        save(user);
        return user;
    }
}
