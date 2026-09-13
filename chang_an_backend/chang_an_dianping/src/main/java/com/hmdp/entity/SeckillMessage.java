package com.hmdp.entity;

import com.baomidou.mybatisplus.annotation.IdType;
import com.baomidou.mybatisplus.annotation.TableId;
import com.baomidou.mybatisplus.annotation.TableName;
import lombok.Data;
import lombok.EqualsAndHashCode;
import lombok.experimental.Accessors;

import java.io.Serializable;
import java.time.LocalDateTime;

@Data
@EqualsAndHashCode(callSuper = false)
@Accessors(chain = true)//开启链式调用
@TableName("tb_seckill_message")
public class SeckillMessage implements Serializable {

    private static final long serialVersionUID = 1L;

    @TableId(value = "id", type = IdType.AUTO)
    private Long id;

    private Long userId;

    private Long voucherId;

    private Long orderId;

    /**
     * 0=待发送 1=已发送 2=发送失败
     */

    private Integer status;

    private Integer retryCount;

    private String errorMsg;

    private LocalDateTime createTime;

    private LocalDateTime updateTime;
}