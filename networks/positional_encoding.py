import torch
import torchvision
import pydicom
import PIL
import cv2
from PIL import Image

import numpy as np
import tqdm

import matplotlib.pyplot as plt


def crop_image(img, d=32):
    '''Make dimensions divisible by `d`'''

    new_size = (img.size[0] - img.size[0] % d,
                img.size[1] - img.size[1] % d)

    bbox = [
        int((img.size[0] - new_size[0]) / 2),
        int((img.size[1] - new_size[1]) / 2),
        int((img.size[0] + new_size[0]) / 2),
        int((img.size[1] + new_size[1]) / 2),
    ]

    img_cropped = img.crop(bbox)
    return img_cropped


def get_params(opt_over, net, net_input, downsampler=None):
    '''Returns parameters that we want to optimize over.

    Args:
        opt_over: comma separated list, e.g. "net,input" or "net"
        net: network
        net_input: torch.Tensor that stores input `z`
    '''
    opt_over_list = opt_over.split(',')
    params = []

    for opt in opt_over_list:

        if opt == 'net':
            params += [x for x in net.parameters()]
        elif opt == 'down':
            assert downsampler is not None
            params = [x for x in downsampler.parameters()]
        elif opt == 'input':
            if net_input.is_leaf:
                net_input.requires_grad = True
                params += [net_input]
        else:
            assert False, 'what is it?'

    return params


def get_image_grid(images_np, nrow=8):
    '''Creates a grid from a list of images by concatenating them.'''
    images_torch = [torch.from_numpy(x) for x in images_np]
    torch_grid = torchvision.utils.make_grid(images_torch, nrow)

    return torch_grid.numpy()


def plot_image_grid(images_np, nrow=8, factor=1, interpolation='lanczos'):
    """Draws images in a grid

    Args:
        images_np: list of images, each image is np.array of size 3xHxW of 1xHxW
        nrow: how many images will be in one row
        factor: size if the plt.figure
        interpolation: interpolation used in plt.imshow
    """
    n_channels = max(x.shape[0] for x in images_np)

    assert (n_channels == 3) or (n_channels == 1), "images should have 1 or 3 channels"

    images_np = [x if (x.shape[0] == n_channels) else np.concatenate([x, x, x], axis=0) for x in images_np]

    grid = get_image_grid(images_np, nrow)

    plt.figure(figsize=(len(images_np) + factor, 12 + factor))

    if images_np[0].shape[0] == 1:
        plt.imshow(grid[0], cmap='gray', interpolation=interpolation)
    else:
        plt.imshow(grid.transpose(1, 2, 0), interpolation=interpolation)

    plt.show()

    return grid


def load(path):
    """Load PIL image."""
    if path[-4:] == ".dcm":
        img = pydicom.read_file(path)
        img = img.pixel_array
        img = (img - img.min()) / img.max()
        img = Image.fromarray((img * 255).astype(np.uint8))
        path = path.replace(".dcm", ".png")
        img.save(path)
    img = Image.open(path)
    return img


def get_image(path, imsize=-1):
    """Load an image and resize to a cpecific size.

    Args:
        path: path to image
        imsize: tuple or scalar with dimensions; -1 for `no resize`
    """
    img = load(path)

    if isinstance(imsize, int):
        imsize = (imsize, imsize)

    if imsize[0] != -1 and img.size != imsize:
        if imsize[0] > img.size[0]:
            img = img.resize(imsize, Image.BICUBIC)
        else:
            img = img.resize(imsize, Image.ANTIALIAS)

    img_np = pil_to_np(img)

    return img, img_np


def fill_noise(x, noise_type):
    """Fills tensor `x` with noise of type `noise_type`."""
    if noise_type == 'u':
        x.uniform_()
    elif noise_type == 'n':
        x.normal_()
    else:
        assert False


def get_meshgrid(spatial_size):
    # X, Y = np.meshgrid(np.arange(1-spatial_size[1], spatial_size[1], 2) / float(spatial_size[1] - 1),
    #                    np.arange(1-spatial_size[0], spatial_size[0], 2) / float(spatial_size[0] - 1))
    X, Y = np.meshgrid(np.arange(0, spatial_size[1]) / float(spatial_size[1] - 1),
                       np.arange(0, spatial_size[0]) / float(spatial_size[0] - 1))
    meshgrid = np.concatenate([X[None, :], Y[None, :]])
    return meshgrid


def get_input(input_depth, method, spatial_size, noise_type='u', var=1. / 10, freq_dict='log'):
    """Returns a pytorch.Tensor of size (1 x `input_depth` x `spatial_size[0]` x `spatial_size[1]`)
    initialized in a specific way.
    Args:
        input_depth: number of channels in the tensor
        method: `noise` for fillting tensor with noise; `meshgrid` for np.meshgrid
        spatial_size: spatial size of the tensor to initialize
        noise_type: 'u' for uniform; 'n' for normal
        var: a factor, a noise will be multiplicated by. Basically it is standard deviation scaler.
    """
    if isinstance(spatial_size, int):
        spatial_size = (spatial_size, spatial_size)
    if method == 'noise':
        shape = [1, input_depth, spatial_size[0], spatial_size[1]]
        net_input = torch.zeros(shape)

        fill_noise(net_input, noise_type)
        net_input *= var
    elif method == 'meshgrid':
        meshgrid = get_meshgrid(spatial_size)
        net_input = np_to_torch(meshgrid).to(torch.float32)
        net_input = torch.concat((input_depth // 2) * (net_input,), axis=1)
    elif method == 'fourier':
        if freq_dict['method'] == 'log':
            freqs = freq_dict['base'] ** torch.linspace(0., freq_dict['n_freqs'] - 1, steps=freq_dict['n_freqs'])
            net_input = generate_fourier_feature_maps(freqs, spatial_size, only_cosine=freq_dict['cosine_only'])
        else:
            raise ValueError

    elif method == 'infer_freqs':
        if freq_dict['method'] == 'log':
            net_input = freq_dict['base'] ** torch.linspace(0., freq_dict['n_freqs'] - 1, steps=freq_dict['n_freqs'])
        else:
            raise ValueError

    else:
        assert False

    return net_input


def pil_to_np(img_PIL):
    '''Converts image in PIL format to np.array.

    From W x H x C [0...255] to C x W x H [0..1]
    '''
    ar = np.array(img_PIL)

    if len(ar.shape) == 3:
        ar = ar.transpose(2, 0, 1)
    else:
        ar = ar[None, ...]

    return ar.astype(np.float32) / 255.


def np_to_pil(img_np):
    '''Converts image in np.array format to PIL image.

    From C x W x H [0..1] to  W x H x C [0...255]
    '''
    ar = np.clip(img_np * 255, 0, 255).astype(np.uint8)

    if img_np.shape[0] == 1:
        ar = ar[0]
    else:
        ar = ar.transpose(1, 2, 0)

    return Image.fromarray(ar)


def np_to_torch(img_np):
    '''Converts image in numpy.array to torch.Tensor.

    From C x W x H [0..1] to  C x W x H [0..1]
    '''
    return torch.from_numpy(img_np)[None, :]


def torch_to_np(img_var):
    '''Converts an image in torch.Tensor format to np.array.

    From 1 x C x W x H [0..1] to  C x W x H [0..1]
    '''
    return img_var.detach().cpu().numpy()[0]


def optimize(optimizer_type, parameters, closure, LR, num_iter, scheduler_step=-1):
    """Runs optimization loop.

    Args:
        optimizer_type: 'LBFGS' of 'adam'
        parameters: list of Tensors to optimize over
        closure: function, that returns loss variable
        LR: learning rate
        num_iter: number of iterations
    """

    if optimizer_type == 'LBFGS':
        # Do several steps with adam first
        optimizer = torch.optim.Adam(parameters, lr=0.001)
        for j in range(100):
            optimizer.zero_grad()
            closure()
            optimizer.step()

        print('Starting optimization with LBFGS')

        def closure2():
            optimizer.zero_grad()
            return closure()

        optimizer = torch.optim.LBFGS(parameters, max_iter=num_iter, lr=LR, tolerance_grad=-1, tolerance_change=-1)
        optimizer.step(closure2)

    elif optimizer_type == 'adam':
        print('Starting optimization with ADAM')
        optimizer = torch.optim.Adam(parameters, lr=LR)
        if scheduler_step != -1:
            scheduler = torch.optim.lr_scheduler.StepLR(optimizer, scheduler_step, gamma=0.5)
        for j in tqdm.tqdm(range(num_iter)):
            optimizer.zero_grad()
            closure()
            optimizer.step()
            if scheduler_step != -1:
                scheduler.step()
    else:
        assert False


def generate_fourier_feature_maps(net_input, spatial_size, dtype=torch.float32, only_cosine=False):
    meshgrid_np = get_meshgrid(spatial_size)
    meshgrid = torch.from_numpy(meshgrid_np).permute(1, 2, 0).unsqueeze(0).type(dtype)
    vp = net_input * torch.unsqueeze(meshgrid, -1)
    if only_cosine:
        vp_cat = torch.cat((torch.cos(vp),), dim=-1)
    else:
        vp_cat = torch.cat((torch.cos(vp), torch.sin(vp)), dim=-1)
    return vp_cat.flatten(-2, -1).permute(0, 3, 1, 2)
