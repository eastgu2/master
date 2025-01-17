import os
import argparse
import torch
import torch.nn as nn
import torch.nn.functional as F


from torch.utils.data import DataLoader

from src.dataset import CUB as Dataset
from src.sampler import Sampler
from src.train_sampler import Train_Sampler
from src.utils import count_acc, Averager, csv_write, square_euclidean_metric
from model import FewShotModel


from src.test_dataset import CUB as Test_Dataset
from src.test_sampler import Test_Sampler

from torch.autograd import Variable

" User input value "
TOTAL = 30000  # total step of training
PRINT_FREQ = 50  # frequency of print loss and accuracy at training step
VAL_FREQ = 100  # frequency of model eval on validation dataset
SAVE_FREQ = 100  # frequency of saving model
TEST_SIZE = 200  # fixed

" fixed value "
VAL_TOTAL = 100

def Test_phase(model, args, k):  # k = args.nway * args.kshot
    model.eval()
    csv = csv_write(args)
    dataset = Test_Dataset(args.dpath)
    test_sampler = Test_Sampler(dataset._labels, n_way=args.nway, k_shot=args.kshot, query=args.query)
    ## test_loader = DataLoader(dataset=dataset, batch_sampler=test_sampler, num_workers=4, pin_memory=True)
    test_loader = DataLoader(dataset=dataset, batch_sampler=test_sampler, num_workers=0, pin_memory=True)

    print('Test start!')
    for i in range(TEST_SIZE):
        print(i)
        for episode in test_loader:
            data = episode.cuda()
            data_shot, data_query = data[:k], data[k:]

            """ TEST Method """
            """ Predict the query images belong to which classes
            
            At the training phase, you measured logits. 
            The logits can be distance or similarity between query images and 5 images of each classes.
            From logits, you can pick a one class that have most low distance or high similarity.
            
            ex) # when logits is distance
                pred = torch.argmin(logits, dim=1)
            
                # when logits is prob
                pred = torch.argmax(logits, dim=1)
                
            pred is torch.tensor with size [20] and the each component value is zero to four
            """

            n_class = args.nway
            assert data_shot.size(0) == args.nway * args.kshot
            n_support = args.kshot
            n_query = int(args.query/args.nway)


            target_inds = torch.arange(0, n_class).view(n_class, 1, 1).expand(n_class, n_query, 1).long()  # 1.arange : 0~ (n_class-1) 까지 생성  2.view : [n_class, 1, 1]로 reshape 3.expand : 크기가 1인 dim을 반복하여 입력된 차원으로 만들어줌, 여기선 [n_class, 1, 1] > [n_class, n_query, 1] n_query만큼 반복함
            target_inds = Variable(target_inds, requires_grad=False)
    
            if data_query.is_cuda:  # is_cuda : ``True`` if the Tensor is stored on the GPU
                target_inds = target_inds.cuda()
            # x = torch.cat([data_shot.view(n_class * n_support, *data_shot.size()[1:]),
            #            data_query.view(n_class * n_query, *data_query.size()[1:])], 0) #0 차원에xs.view와 xq.view 연결(Concatenates)
            x = data #0 차원에xs.view와 xq.view 연결(Concatenates)
            
            # FewShotModel : class 이름
            # model : 인스턴스
            # z = model.encoder.forward(x)
            z = model.forward(x)
            z_dim = z.size(-1)  #last dim의 size

            z_proto = z[:n_class*n_support].view(n_class, n_support, z_dim).mean(1)
            zq = z[n_class*n_support:]
    
            dists = square_euclidean_metric(zq, z_proto)
            pred = torch.argmin(dists, dim=1)
            print(pred)
            # save your prediction as StudentID_Name.csv file
            csv.add(pred)
    csv.close()
    print('Test finished, check the csv file!')
    exit()


def train(args):
    # the number of N way, K shot images
    k = args.nway * args.kshot

    # Train data loading
    dataset = Dataset(args.dpath, state='train')
    train_sampler = Train_Sampler(dataset._labels, n_way=args.nway, k_shot=args.kshot, query=args.query)
    data_loader = DataLoader(dataset=dataset, batch_sampler=train_sampler, num_workers=0, pin_memory=True)
    ## data_loader = DataLoader(dataset=dataset, batch_sampler=train_sampler, num_workers=4, pin_memory=True)
    
    # Validation data loading
    val_dataset = Dataset(args.dpath, state='val')
    val_sampler = Sampler(val_dataset._labels, n_way=args.nway, k_shot=args.kshot, query=args.query)
    val_data_loader = DataLoader(dataset=val_dataset, batch_sampler=val_sampler, num_workers=0, pin_memory=True)
    ## val_data_loader = DataLoader(dataset=val_dataset, batch_sampler=val_sampler, num_workers=4, pin_memory=True)

    """ TODO 1.a """
    " Make your own model for Few-shot Classification in 'model.py' file."

    # model setting
    
    model = FewShotModel()
    #model = Resnet12(180,0.2)
    """ TODO 1.a END """
    # pretrained model load
    if args.restore_ckpt is not None:
        print('pretrained model load')
        state_dict = torch.load(args.restore_ckpt)
        model.load_state_dict(state_dict)
    model.cuda()    #gpu사용
    model.train()
    if args.test_mode == 1:
        Test_phase(model, args, k)

    """ TODO 1.b (optional) """
    " Set an optimizer or scheduler for Few-shot classification (optional) "

    # Default optimizer setting
    optimizer = torch.optim.Adam(model.parameters(), lr=0.001)

    """ TODO 1.b (optional) END """

    tl = Averager()  # save average loss
    ta = Averager()  # save average accuracy

    # training start
    print('train start')
    max_abc = 0
    for i in range(TOTAL):
        for episode in data_loader:
            optimizer.zero_grad()
            data, label = [_.cuda() for _ in episode]  # load an episode
            
            # split an episode images and labels into shots and query set
            # note! data_shot shape is ( nway * kshot, 3, h, w ) not ( kshot * nway, 3, h, w )
            # Take care when reshape the data shot
            data_shot, data_query = data[:k], data[k:]      # k = args.nway * args.kshot
                                                            # data_shot : torch.Size([25, 3, 400, 400]) data_query : torch.Size([20, 3, 400, 400])
            label_shot, label_query = label[:k], label[k:]  # label_shot : torch.Size([25]) label_query : torch.Size([20])
            label_shot = sorted(list(set(label_shot.tolist())))

            # convert labels into 0-4 values
            label_query = label_query.tolist() # .tolist : array를 list로 바꿔줌
            labels = []
            
            for j in range(len(label_query)): # len(label_query) = 20
                label = label_shot.index(label_query[j]) #label_shot에서 label_query[j]랑 같은 data가 몇번째 index에 있는지 출력해줌
                labels.append(label)
            labels = torch.tensor(labels).cuda()
            """ TODO 2 ( Same as above TODO 2 ) """
            """ Train the model 
            Input:
                data_shot : torch.tensor, shot images, [args.nway * args.kshot, 3, h, w] 
                            be careful when using torch.reshape or .view functions
                data_query : torch.tensor, query images, [args.query, 3, h, w]
                labels : torch.tensor, labels of query images, [args.query]
            output:
                loss : torch scalar tensor which used for updating your model
                logits : A value to measure accuracy and loss
            """

            n_class = args.nway
            assert data_shot.size(0) == args.nway * args.kshot
            n_support = args.kshot
            n_query = int(args.query/args.nway)

            target_inds = torch.arange(0, n_class).view(n_class, 1, 1).expand(n_class, n_query, 1).long()  # 1.arange : 0~ (n_class-1) 까지 생성  2.view : [n_class, 1, 1]로 reshape 3.expand : 크기가 1인 dim을 반복하여 입력된 차원으로 만들어줌, 여기선 [n_class, 1, 1] > [n_class, n_query, 1] n_query만큼 반복함
            target_inds = Variable(target_inds, requires_grad=False)
    
            if data_query.is_cuda:  # is_cuda : ``True`` if the Tensor is stored on the GPU
                target_inds = target_inds.cuda()
            x = data # 0 차원에xs.view와 xq.view 연결(Concatenates)

            z = model.forward(x)
            z_dim = z.size(-1)  #last dim의 size
    
            z_proto = z[:n_class*n_support].view(n_class, n_support, z_dim).mean(1)
            zq = z[n_class*n_support:]
    
            dists = square_euclidean_metric(zq, z_proto)
            logits = dists
            log_p_y = F.log_softmax(-dists, dim=1).view(n_class, n_query, -1)
    
            loss = -log_p_y.gather(2, target_inds).squeeze().view(-1).mean()
            
            _, y_hat = log_p_y.max(2)
            
            """ TODO 2 END """
            
            acc = count_acc(logits, labels)
            tl.add(loss.item())
            ta.add(acc)
            
            loss.backward()
            optimizer.step()

            proto = None; logits = None; loss = None

        if (i+1) % PRINT_FREQ == 0:
            print('train {}, loss={:.4f} acc={:.4f}'.format(i+1, tl.item(), ta.item()))

            # initialize loss and accuracy mean
            tl = None
            ta = None
            tl = Averager()
            ta = Averager()

        # validation start
        if (i+1) % VAL_FREQ == 0:
            print('validation start')
            model.eval()
            with torch.no_grad():
                vl = Averager()  # save average loss
                va = Averager()  # save average accuracy
                for j in range(VAL_TOTAL): #VAL_TOTAL = 100
                    for episode in val_data_loader:

                        data, label = [_.cuda() for _ in episode]

                        data_shot, data_query = data[:k], data[k:] # load an episode

                        label_shot, label_query = label[:k], label[k:]
                        label_shot = sorted(list(set(label_shot.tolist())))

                        label_query = label_query.tolist()

                        labels = []
                        for j in range(len(label_query)):
                            label = label_shot.index(label_query[j])
                            labels.append(label)
                        labels = torch.tensor(labels).cuda()

                        """ TODO 2 ( Same as above TODO 2 ) """
                        """ Train the model 
                        Input:
                            data_shot : torch.tensor, shot images, [args.nway * args.kshot, 3, h, w]
                                        be careful when using torch.reshape or .view functions
                            data_query : torch.tensor, query images, [args.query, 3, h, w]
                            labels : torch.tensor, labels of query images, [args.query]
                        output:
                            loss : torch scalar tensor which used for updating your model
                            logits : A value to measure accuracy and loss
                        """

                        n_class = args.nway
                        assert data_shot.size(0) == args.nway * args.kshot
                        n_support = args.kshot
                        n_query = int(args.query/args.nway)
            
            
                        target_inds = torch.arange(0, n_class).view(n_class, 1, 1).expand(n_class, n_query, 1).long()  # 1.arange : 0~ (n_class-1) 까지 생성  2.view : [n_class, 1, 1]로 reshape 3.expand : 크기가 1인 dim을 반복하여 입력된 차원으로 만들어줌, 여기선 [n_class, 1, 1] > [n_class, n_query, 1] n_query만큼 반복함
                        target_inds = Variable(target_inds, requires_grad=False)
                
                        if data_query.is_cuda:  # is_cuda : ``True`` if the Tensor is stored on the GPU
                            target_inds = target_inds.cuda()

                        x = data 

                        z = model.forward(x)
                        z_dim = z.size(-1)  #last dim의 size
                        z_proto = z[:n_class*n_support].view(n_class, n_support, z_dim).mean(1)
                        zq = z[n_class*n_support:]
                
                        logits = square_euclidean_metric(zq, z_proto)
                        log_p_y = F.log_softmax(-dists, dim=1).view(n_class, n_query, -1)
                
                        loss = -log_p_y.gather(2, target_inds).squeeze().view(-1).mean()

                        _, y_hat = log_p_y.max(2)

                        

                        """ TODO 2 END """

                        acc = count_acc(logits, labels)

                        vl.add(loss.item())
                        va.add(acc)

                        proto = None; logits = None; loss = None
                abc = va.item()
                print('val loss mean : %.4f  val accuracy mean :                !!!!!%.4f!!!!! (%d)' % (vl.item(), va.item(), i+1))

                # initialize loss and accuracy mean
                vl = None
                va = None
                vl = Averager()
                va = Averager()
            model.train()

        if (i+1) % SAVE_FREQ == 0:
            if abc> 0.55:
                PATH = 'checkpoints\\%d_%s_%d.pth' % (i + 1, args.name, (abc*100))
                torch.save(model.state_dict(), PATH)
            if abc > max_abc:            
                max_abc = abc
                print('                                                                 MAX')

                


if __name__ == '__main__':

    parser = argparse.ArgumentParser()
    parser.add_argument('--name', default='model', help="name your experiment")
    parser.add_argument('--dpath', '--d', default='..\\CUB_200_2011', type=str,
                        help='the path where dataset is located')
    parser.add_argument('--nway', '--n', default=5, type=int, help='number of class in the support set (5 or 20)')
    parser.add_argument('--kshot', '--k', default=5, type=int,
                        help='number of data in each class in the support set (1 or 5)')
    parser.add_argument('--query', '--q', default=20, type=int, help='number of query data')
    parser.add_argument('--ntest', default=10, type=int, help='number of tests')
    parser.add_argument('--gpus', type=int, nargs='+', default=0)
    parser.add_argument('--restore_ckpt', default='./checkpoints/11200_model.pth', type=str, help="restore checkpoint") # test
    parser.add_argument('--test_mode', type=int, default=1, help="if you want to test the model, change the value to 1") #test
#    parser.add_argument('--restore_ckpt', type=str, help="restore checkpoint") # train
#    parser.add_argument('--test_mode', type=int, default=0, help="if you want to test the model, change the value to 1") #Train

    args = parser.parse_args()

    if not os.path.isdir('checkpoints'):
        os.mkdir('checkpoints')

    torch.cuda.set_device(args.gpus)


    train(args)
