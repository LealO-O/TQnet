import torch
import torch.nn as nn

class Model(nn.Module):
    def __init__(self, configs):
        super(Model, self).__init__()

        self.seq_len = configs.seq_len
        self.pred_len = configs.pred_len
        self.enc_in = configs.enc_in
        self.cycle_len = configs.cycle
        self.model_type = configs.model_type
        self.d_model = configs.d_model
        self.dropout = configs.dropout
        self.use_revin = configs.use_revin

        self.use_tq = True  # ablation parameter, default: True
        self.channel_aggre = True   # ablation parameter, default: True

        if self.use_tq:
            self.temporalQuery = torch.nn.Parameter(torch.zeros(self.cycle_len, self.enc_in), requires_grad=True)
            self.temporalQuery_week = torch.nn.Parameter(torch.zeros(self.cycle_len, self.enc_in), requires_grad=True) # 周周期

        if self.channel_aggre:
            self.channelAggregator = nn.MultiheadAttention(embed_dim=self.seq_len, num_heads=4, batch_first=True, dropout=0.5)

        self.input_proj = nn.Linear(self.seq_len, self.d_model)

        self.model = nn.Sequential(
            nn.Linear(self.d_model, self.d_model),
            nn.GELU(),
            nn.Linear(self.d_model, self.d_model),
            nn.GELU(),
        )

        self.output_proj = nn.Sequential(
            nn.Dropout(self.dropout),
            nn.Linear(self.d_model, self.pred_len)
        )
        self.device = torch.device("cuda:0" if torch.cuda.is_available() else "cpu")
        self.alpha = nn.Parameter(torch.tensor(0.5).to(self.device))
        self.beta = nn.Parameter(torch.tensor(0.5).to(self.device))



    def forward(self, x, cycle_index, week_cycle_index):

        # instance norm
        if self.use_revin:
            seq_mean = torch.mean(x, dim=1, keepdim=True)
            seq_var = torch.var(x, dim=1, keepdim=True) + 1e-5
            x = (x - seq_mean) / torch.sqrt(seq_var)

        # b,s,c -> b,c,s
        x_input = x.permute(0, 2, 1)

        if self.use_tq:
            gather_index = (cycle_index.view(-1, 1) + torch.arange(self.seq_len, device=cycle_index.device).view(1, -1)) % self.cycle_len
            query_input = self.temporalQuery[gather_index].permute(0, 2, 1)  # (b, c, s)
            
            week_gather_index = (week_cycle_index.view(-1, 1) + torch.arange(self.seq_len, device=cycle_index.device).view(1, -1)) % self.cycle_len
            week_query_input = self.temporalQuery_week[week_gather_index].permute(0, 2, 1)
            
            # print("gather_index shape:", gather_index.shape)# gather_index shape: torch.Size([256, 96])
            # print("query_input shape:", query_input.dtype) # query_input shape: torch.Size([256, 7, 96]) torch.float32
            # print("week_gather_index shape:", week_gather_index.shape) # week_gather_index shape: torch.Size([256, 96])
            # print("week_query_input shape:", week_query_input.shape)
            
            # query_input = torch.cat([query_input, week_query_input], dim=-1)
            ### 逐元素加权和
            
            query_input = self.alpha * query_input + self.beta * week_query_input  # 96维
            # print("query_input shape:", query_input.dtype) #torch.Size([256, 7, 192])
            # print(query_input.device, query_input.dtype,x_input.device, x_input.dtype) #before: cuda:0 torch.float32 cuda:0 torch.float32 after cuda:0 torch.float32 cuda:0 torch.float32
            if self.channel_aggre:
                channel_information = self.channelAggregator(query=query_input, key=x_input, value=x_input)[0]
            else:
                channel_information = query_input
        else:
            if self.channel_aggre:
                channel_information = self.channelAggregator(query=x_input, key=x_input, value=x_input)[0]
            else:
                channel_information = 0

        input = self.input_proj(x_input+channel_information)

        hidden = self.model(input)

        output = self.output_proj(hidden+input).permute(0, 2, 1)

        # instance denorm
        if self.use_revin:
            output = output * torch.sqrt(seq_var) + seq_mean

        return output


